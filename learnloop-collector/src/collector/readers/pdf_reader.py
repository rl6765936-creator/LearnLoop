"""PDF text extraction with pypdf (pure Python, works offline, sends nothing anywhere)."""

from __future__ import annotations

from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from collector.readers.base import ReaderError, Segment
from collector.readers.file_types import require_pdf

import logging

# pypdf prints messages such as "EOF marker not found" for damaged files. The Collector already
# reports a damaged file itself (a clear failure), so these technical messages are not shown.
logging.getLogger("pypdf").setLevel(logging.ERROR)


def read_pdf(f: BinaryIO) -> tuple[list[Segment], list[int]]:
    """Returns (segments from the text layer, page numbers that have NO text -> need OCR)."""
    require_pdf(f)
    try:
        reader = PdfReader(f)
        if reader.is_encrypted:
            raise ReaderError("PDF is password-protected")
        segments, empty_pages = [], []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                segments.append(Segment(f"page {number}", text))
            else:
                empty_pages.append(number)
        return segments, empty_pages
    except ReaderError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as e:
        raise ReaderError(f"PDF is corrupted or unreadable ({type(e).__name__})")
