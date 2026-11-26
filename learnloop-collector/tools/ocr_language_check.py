"""Diagnostic for finding 4: what does the English-only Windows OCR return for Hebrew text?

Run on Windows, from the project folder, with the venv active:
    $env:PYTHONPATH = "src"; python tools\\ocr_language_check.py

Safety / privacy:
- Uses ONLY synthetic images drawn in memory by this script (no study files are read).
- Writes nothing to disk and starts no external program. Uses the same OCR engine as the
  Collector (Windows built-in OCR, inside this Python process). Nothing leaves the computer.
- Does NOT change the OCR decision or enable Tesseract.

Note: Pillow draws Hebrew letters in typing order (it may not reverse them visually);
for this check that does not matter – what matters is how the engine reacts to
Hebrew letter shapes.
"""

from __future__ import annotations

import sys

SAMPLES = {
    "english": "Binary search tree\nInsert and delete in O(log n)",
    "hebrew": "עץ חיפוש בינארי\nהכנסה ומחיקה בזמן לוגריתמי",
    "mixed": "מחסנית Stack\nפעולות push ו-pop",
}
FONT_CANDIDATES = [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"]


def draw(text: str):
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for candidate in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(candidate, 48)
            break
        except OSError:
            continue
    if font is None:
        sys.exit("No Windows font with Hebrew letters was found (arial.ttf / segoeui.ttf).")
    image = Image.new("RGB", (1400, 260), "white")
    ImageDraw.Draw(image).multiline_text((40, 40), text, fill="black", font=font, spacing=30)
    return image


def counts(text: str) -> str:
    latin = sum("a" <= c.lower() <= "z" for c in text)
    hebrew = sum("\u0590" <= c <= "\u05ff" for c in text)
    digits = sum(c.isdigit() for c in text)
    return f"{len(text)} chars, latin={latin}, hebrew={hebrew}, digits={digits}"


def main() -> None:
    from collector.ocr.engine import get_engine
    engine = get_engine()
    if engine is None:
        sys.exit("No OCR engine is available on this computer.")
    print(f"OCR engine: {engine.name}, languages: {', '.join(engine.languages)}\n")
    for name, text in SAMPLES.items():
        result = engine.recognize(draw(text))
        print(f"--- {name} ---")
        print("drawn:      ", text.replace("\n", " / "))
        print("recognized: ", (result.replace("\n", " / ") or "(empty)"))
        print("stats:      ", counts(result))
        print()
    print("Please send this whole output back. It decides how an unreliable OCR result is shown.")


if __name__ == "__main__":
    main()
