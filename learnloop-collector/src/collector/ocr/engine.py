"""The OCR engine interface.

Any engine (Windows built-in now, Tesseract later if needed) only has to provide
`name` and `recognize(image) -> text`. The rest of the Collector never knows which
engine is used, so switching engines changes ONE place.
"""

from __future__ import annotations

from typing import Protocol

# User decision: Hebrew + English.
LANGUAGES = ("he", "en")


class OcrError(Exception):
    """The OCR engine is missing, misconfigured, or failed on an image."""


class OcrEngine(Protocol):
    name: str

    def recognize(self, image) -> str:  # image: PIL.Image.Image
        ...


_cached: tuple[bool, OcrEngine | None] = (False, None)


def get_engine() -> OcrEngine | None:
    """The engine to use, or None if no engine is available on this computer."""
    global _cached
    if _cached[0]:
        return _cached[1]
    # User decision (2026-11-23): the prototype must not run any external program.
    # Only Windows' own OCR (inside this Python process) is used. It has no Hebrew,
    # so in the prototype OCR works for English only. Hebrew is planned for later
    # (Tesseract code exists in tesseract_ocr.py but is NOT used).
    engine: OcrEngine | None = None
    try:
        from collector.ocr.windows_ocr import WindowsOcr
        engine = WindowsOcr(LANGUAGES)
    except (ImportError, OcrError):
        engine = None
    _cached = (True, engine)
    return engine
