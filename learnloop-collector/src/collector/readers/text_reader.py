"""Plain-text formats: TXT, Markdown, CSV, JSON and source code.

Source code is read as TEXT ONLY – it is never imported, compiled or executed.
"""

from __future__ import annotations

from typing import BinaryIO

from collector.readers.base import MAX_TEXT_FILE_BYTES, ReaderError, Segment
from collector.readers.file_types import looks_binary

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json",
    ".py", ".cs", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".html", ".css", ".sql", ".xml", ".yaml", ".yml", ".sh", ".ps1", ".go", ".rs", ".kt",
}

LINES_PER_SEGMENT = 200


def decode(raw: bytes) -> tuple[str, str]:
    """Return (text, encoding). Tries the encodings common for Hebrew text files."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    if looks_binary(raw[:8192]):
        raise ReaderError("binary content with a text extension (misleading extension?)")
    for encoding in ("utf-8", "cp1255"):  # cp1255 = older Windows Hebrew
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ReaderError("unknown text encoding")


def read_text(f: BinaryIO, size: int) -> list[Segment]:
    if size > MAX_TEXT_FILE_BYTES:
        raise ReaderError(f"text file too large ({size:,} bytes)")
    text, _encoding = decode(f.read())
    lines = text.splitlines()
    segments = []
    for start in range(0, len(lines), LINES_PER_SEGMENT):
        chunk = lines[start:start + LINES_PER_SEGMENT]
        body = "\n".join(chunk)
        if body.strip():
            end = start + len(chunk)
            segments.append(Segment(f"lines {start + 1}-{end}", body))
    return segments
