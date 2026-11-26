"""Open images safely and turn scanned PDF pages into images."""

from __future__ import annotations

from typing import BinaryIO

from collector.readers.base import ReaderError

# Spec GAP: limits not defined. [ASSUMPTION]s:
MAX_IMAGE_PIXELS = 40_000_000   # ~ 8000 x 5000. Larger images may be "decompression bombs".
MAX_OCR_PAGES = 30              # scanned pages OCR-ed per PDF
RENDER_DPI = 200                # resolution for scanned PDF pages (good OCR / speed balance)
ALLOWED_FORMATS = {"PNG", "JPEG", "BMP", "GIF", "TIFF", "WEBP"}


def load_image(f: BinaryIO):
    """Open an image from a read-only file. Checks the real format and the size first."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(f)          # reads only the header at this point
    except (UnidentifiedImageError, OSError):
        raise ReaderError("content is not an image (misleading extension?)")
    if image.format not in ALLOWED_FORMATS:
        raise ReaderError(f"unsupported image format {image.format}")
    width, height = image.size
    if width * height > MAX_IMAGE_PIXELS:
        raise ReaderError(f"image too large ({width}x{height} pixels)")
    try:
        image.load()                   # now decode the pixels
    except (OSError, ValueError) as e:
        raise ReaderError(f"image is corrupted ({type(e).__name__})")
    return image.convert("RGB")


def render_pdf_pages(data: bytes, page_numbers: list[int]):
    """Yield (page number, image) for the given 1-based pages. Uses pdfium, locally."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    try:
        for number in page_numbers:
            page = document[number - 1]
            try:
                bitmap = page.render(scale=RENDER_DPI / 72)
                yield number, bitmap.to_pil().convert("RGB")
            finally:
                page.close()
    finally:
        document.close()
