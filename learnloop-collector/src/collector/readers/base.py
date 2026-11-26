"""Shared types and limits for all readers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Limits protect against huge or malicious files ("zip bombs").
# The exact numbers are an [ASSUMPTION] – the spec does not define them.
MAX_FILE_BYTES = 50 * 1024 * 1024          # 50 MB on disk
MAX_TEXT_FILE_BYTES = 10 * 1024 * 1024     # 10 MB for plain-text formats
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024 # 200 MB inside DOCX/PPTX/XLSX (they are ZIP files)
MAX_TEXT_CHARS = 2_000_000                 # cap on extracted text

# Status values
OK = "ok"                    # text extracted
EMPTY = "empty"              # file readable but contains no text
NEEDS_OCR = "needs_ocr"      # no text layer (scanned PDF / image) -> Stage 6
FAILED = "failed"            # could not be read (corrupted, encrypted, wrong type...)
UNSUPPORTED = "unsupported"  # format not supported
BLOCKED = "blocked"          # PathGuard refused access – file was not opened
NOT_FOUND = "not_found"      # path is allowed, but no such file exists


class ReaderError(Exception):
    """A clear, user-facing reason why a file could not be read."""


@dataclass
class Segment:
    """A piece of text + where it came from (spec: traceability to the exact segment)."""
    location: str   # e.g. "page 3", "slide 2", "sheet Grades row 5", "lines 1-200"
    text: str


@dataclass
class ExtractionResult:
    path: Path
    status: str
    reader: str = ""
    segments: list[Segment] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.segments)


def cap_segments(segments: list[Segment], warnings: list[str]) -> list[Segment]:
    """Stop collecting text after MAX_TEXT_CHARS, and record that we did."""
    total, kept = 0, []
    for seg in segments:
        if total + len(seg.text) > MAX_TEXT_CHARS:
            warnings.append(f"text truncated after {MAX_TEXT_CHARS:,} characters")
            break
        kept.append(seg)
        total += len(seg.text)
    return kept
