"""Windows built-in OCR (Windows.Media.Ocr), reached through the pywinrt packages.

It runs INSIDE this Python process, using Windows' own OCR component:
no external program is started and no data leaves the computer.
On the user's computer only English (en-US) is installed; Hebrew is not available
for Windows OCR at all (checked 2026-11-23) – Hebrew OCR is planned for later.
"""

from __future__ import annotations

import asyncio

from collector.ocr.engine import OcrError

try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine as _WinOcrEngine
    from winrt.windows.storage.streams import DataWriter
except ImportError as e:  # not on Windows, or packages not installed
    raise ImportError(f"Windows OCR packages are not available: {e}") from e


def available_languages() -> list[str]:
    return [lang.language_tag for lang in _WinOcrEngine.available_recognizer_languages]


class WindowsOcr:
    name = "windows"

    def __init__(self, languages: tuple[str, ...]):
        self.engines = []
        for tag in languages:
            language = Language(tag)
            if _WinOcrEngine.is_language_supported(language):
                self.engines.append((tag, _WinOcrEngine.try_create_from_language(language)))
        if not self.engines:
            raise OcrError(f"none of the OCR languages {languages} is installed in Windows "
                           f"(installed: {available_languages()})")
        self.languages = [tag for tag, _ in self.engines]
        self.max_dimension = _WinOcrEngine.max_image_dimension

    def recognize(self, image) -> str:
        bitmap = _to_bitmap(_fit(image, self.max_dimension))
        # With several languages, keep the result that recognized the most letters/digits.
        # [ASSUMPTION] – today only English is available, so there is one engine.
        results = [asyncio.run(_recognize(engine, bitmap)) for _, engine in self.engines]
        return max(results, key=lambda text: sum(ch.isalnum() for ch in text), default="")


def _fit(image, max_dimension: int):
    width, height = image.size
    scale = min(1.0, max_dimension / max(width, height))
    if scale < 1.0:
        image = image.resize((int(width * scale), int(height * scale)))
    return image


def _to_bitmap(image):
    """Pillow image -> Windows SoftwareBitmap, entirely in memory."""
    rgba = image.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(rgba.tobytes())
    return SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8,
                                                  rgba.width, rgba.height)


async def _recognize(engine, bitmap) -> str:
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines)
