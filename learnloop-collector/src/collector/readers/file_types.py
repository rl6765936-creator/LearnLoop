"""Check that a file's CONTENT matches its extension (misleading extensions).

We look at the first bytes ("magic bytes") instead of trusting the file name.
"""

from __future__ import annotations

import zipfile
from typing import BinaryIO

from collector.readers.base import MAX_UNCOMPRESSED_BYTES, ReaderError

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"

# Each Office format is a ZIP that must contain this internal file.
OFFICE_MARKERS = {
    ".docx": "word/document.xml",
    ".pptx": "ppt/presentation.xml",
    ".xlsx": "xl/workbook.xml",
}


def require_pdf(f: BinaryIO) -> None:
    head = f.read(1024)
    f.seek(0)
    if PDF_MAGIC not in head:
        raise ReaderError("content is not a PDF (misleading extension?)")


def require_office(f: BinaryIO, extension: str) -> None:
    head = f.read(4)
    f.seek(0)
    if head != ZIP_MAGIC:
        raise ReaderError(f"content is not a {extension} file (misleading extension?)")
    try:
        with zipfile.ZipFile(f) as z:
            names = set(z.namelist())
            total = sum(info.file_size for info in z.infolist())
    except zipfile.BadZipFile:
        raise ReaderError("file is corrupted (broken ZIP structure)")
    finally:
        f.seek(0)
    if OFFICE_MARKERS[extension] not in names:
        raise ReaderError(f"content is not a {extension} file (misleading extension?)")
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ReaderError("file expands to too much data (possible zip bomb)")


def looks_binary(sample: bytes) -> bool:
    """Text files do not contain NUL bytes – unless they are UTF-16 (checked separately)."""
    return b"\x00" in sample
