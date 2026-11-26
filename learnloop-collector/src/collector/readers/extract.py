"""extract_file – the ONLY entry point for reading a file's content.

Flow:  PathGuard check -> size check -> open read-only -> pick reader by extension
       -> reader verifies content type -> text + segments.
Any problem becomes a clear status; nothing raises to the caller.
"""

from __future__ import annotations

import os
from pathlib import Path

from collector.permissions.path_guard import PathGuard
from collector.readers import base
from collector.readers.base import ExtractionResult, ReaderError
from collector.readers.base import Segment
from collector.readers.text_reader import TEXT_EXTENSIONS, read_text

_DEFAULT = object()  # "use the engine installed on this computer"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


class _AccessChanged(Exception):
    """Permission was revoked or paused while the file was being read."""


def _ocr_language_warning(ocr, warnings: list[str]) -> None:
    """Make the OCR language limit visible (finding 4).

    The prototype OCR is English-only by decision. Text in another language (e.g. Hebrew)
    cannot be recognized and may come out as random-looking Latin letters. The Collector
    does not judge the text; it only records this technical fact, once per file, so the
    user (and later the AI) know the OCR text may be unreliable.
    """
    from collector.ocr.engine import LANGUAGES
    available = getattr(ocr, "languages", None)
    if available is None:
        return
    available = {str(lang).split("-")[0].lower() for lang in available}
    missing = [lang for lang in LANGUAGES if lang not in available]
    if missing:
        warnings.append(f"OCR ran without language(s) {', '.join(missing)}: text in those "
                        f"languages is not recognized and may appear garbled")


# Decision 2026-11-24 (option A, APPROVED): text that comes ONLY from OCR is not used. Windows OCR
# here reads English only, Hebrew comes out garbled, and the Collector may not judge whether text
# "makes sense". Such files go to the unreadable list: she can ignore them or type their content.
# Switch to True only with a new decision (e.g. if Hebrew OCR becomes available).
USE_OCR_TEXT = False
OCR_HELD = ("text in images and scanned pages is not used (decision 24.11): "
            "ignore the file or type its content")


def _ocr_image(f, ocr, warnings: list[str]) -> list[Segment]:
    from collector.ocr.images import load_image
    if not USE_OCR_TEXT:
        raise ReaderError(OCR_HELD)
    if ocr is None:
        raise ReaderError("image needs OCR, but no OCR engine is available")
    _ocr_language_warning(ocr, warnings)
    image = load_image(f)
    text = _run_ocr(ocr, image)
    if not text.strip():
        raise ReaderError("OCR found no text in the image")
    return [Segment("image (OCR)", text)]


def _read_pdf_with_ocr(f, ocr, warnings: list[str], still_allowed) -> list[Segment]:
    """Text layer first; only pages WITHOUT text are rendered and OCR-ed."""
    from collector.ocr.images import MAX_OCR_PAGES, render_pdf_pages
    from collector.readers.pdf_reader import read_pdf

    segments, empty_pages = read_pdf(f)
    if not empty_pages:
        return segments
    if not USE_OCR_TEXT:
        if not segments:
            raise ReaderError(OCR_HELD)
        warnings.append(f"{len(empty_pages)} scanned page(s) without text were not used (decision 24.11)")
        return segments
    if ocr is None:
        if not segments:
            raise ReaderError("scanned PDF needs OCR, but no OCR engine is available")
        warnings.append(f"{len(empty_pages)} page(s) without text were skipped (no OCR engine)")
        return segments
    if len(empty_pages) > MAX_OCR_PAGES:
        warnings.append(f"only the first {MAX_OCR_PAGES} of {len(empty_pages)} scanned pages were OCR-ed")
        empty_pages = empty_pages[:MAX_OCR_PAGES]
    _ocr_language_warning(ocr, warnings)
    f.seek(0)
    try:
        for number, image in render_pdf_pages(f.read(), empty_pages):
            # OCR of many pages can take a while: stop between pages if permission changed.
            if still_allowed is not None and not still_allowed():
                raise _AccessChanged()
            text = _run_ocr(ocr, image)
            if text.strip():
                segments.append(Segment(f"page {number} (OCR)", text))
    except (ReaderError, _AccessChanged):
        raise
    except Exception as e:
        raise ReaderError(f"could not render PDF pages for OCR ({type(e).__name__})")
    segments.sort(key=lambda s: int(s.location.split()[1]))  # keep page order
    if not segments:
        raise ReaderError("OCR found no text in the scanned PDF")
    return segments


def _run_ocr(ocr, image) -> str:
    try:
        return ocr.recognize(image)
    except Exception as e:  # engine failure must not crash the Collector
        raise ReaderError(f"OCR engine failed ({type(e).__name__})")


def _reader_for(extension: str, ocr, warnings: list[str], still_allowed=None):
    """Import document libraries only when needed."""
    if extension in TEXT_EXTENSIONS:
        return "text", lambda f, size: read_text(f, size)
    if extension in IMAGE_EXTENSIONS:
        return "image", lambda f, size: _ocr_image(f, ocr, warnings)
    if extension == ".pdf":
        return "pdf", lambda f, size: _read_pdf_with_ocr(f, ocr, warnings, still_allowed)
    if extension in (".docx", ".pptx", ".xlsx"):
        from collector.readers import office_readers
        func = {".docx": office_readers.read_docx,
                ".pptx": office_readers.read_pptx,
                ".xlsx": office_readers.read_xlsx}[extension]
        return extension[1:], lambda f, size: func(f)
    return None, None


def extract_file(path: os.PathLike | str, guard: PathGuard, ocr=_DEFAULT,
                 still_allowed=None) -> ExtractionResult:
    """ocr: an OCR engine, None for "no OCR", or omitted to use the installed engine.

    still_allowed: optional function that re-reads the LIVE settings and answers
    "is the permission this read started under still valid?". The guard holds a copy of
    the settings taken earlier, so only this function can notice a revoke/pause that
    happened while the file was being read.
    """
    decision = guard.check(path)
    if not decision.allowed:
        status = base.NOT_FOUND if decision.reason == "not found" else base.BLOCKED
        return ExtractionResult(Path(path), status, error=decision.reason)

    real = decision.path
    if real.is_dir():
        return ExtractionResult(real, base.UNSUPPORTED, error="this is a folder")

    extension = real.suffix.lower()
    warnings: list[str] = []
    if ocr is _DEFAULT and (extension in IMAGE_EXTENSIONS or extension == ".pdf"):
        from collector.ocr.engine import get_engine
        ocr = get_engine()
    elif ocr is _DEFAULT:
        ocr = None
    name, reader = _reader_for(extension, ocr, warnings, still_allowed)
    if reader is None:
        return ExtractionResult(real, base.UNSUPPORTED,
                                error=f"format '{extension or 'no extension'}' is not supported")

    try:
        with open(real, "rb") as f:  # "rb" = read-only, binary. We never open for writing.
            size = os.fstat(f.fileno()).st_size  # size of the file we actually opened
            if size > base.MAX_FILE_BYTES:
                raise ReaderError(f"file too large ({size:,} bytes)")
            if size == 0:
                return ExtractionResult(real, base.EMPTY, reader=name)
            segments = reader(f, size)
    except _AccessChanged:
        return ExtractionResult(real, base.BLOCKED, reader=name,
                                error="permission revoked or paused while reading – result discarded")
    except ReaderError as e:
        return ExtractionResult(real, base.FAILED, reader=name, error=str(e))
    except PermissionError:
        return ExtractionResult(real, base.FAILED, reader=name,
                                error="no permission to read (file locked or permissions changed)")
    except OSError as e:
        return ExtractionResult(real, base.FAILED, reader=name, error=f"read error: {e.strerror}")
    except Exception as e:  # unexpected library failure on a malformed file
        return ExtractionResult(real, base.FAILED, reader=name,
                                error=f"unexpected error while parsing ({type(e).__name__})")

    # Re-check after reading. guard.check notices a file that moved or became a link
    # meanwhile; still_allowed (if given) notices a revoke/pause in the live settings.
    # (Narrows, but does not fully close, the time-of-check gap.)
    if not guard.check(real).allowed or (still_allowed is not None and not still_allowed()):
        return ExtractionResult(real, base.BLOCKED, reader=name,
                                error="access changed while reading – result discarded")

    segments = base.cap_segments(segments, warnings)
    if not segments:
        return ExtractionResult(real, base.EMPTY, reader=name, warnings=warnings)
    return ExtractionResult(real, base.OK, reader=name, segments=segments, warnings=warnings)
