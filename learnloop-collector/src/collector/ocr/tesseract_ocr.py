"""Tesseract OCR – a separate, free program installed on the computer.

NOT USED in the prototype (user decision 2026-11-23: no external programs).
Kept for the future Hebrew OCR work; enabling it requires an explicit user decision.

How we call it, and why it is safe:
- We run tesseract.exe from a FIXED, known location (not from the study folder).
- The image is passed through memory (stdin) and the text comes back through stdout,
  so no temporary copies of study material are written to disk.
- Every call has a time limit, so a strange image cannot freeze the Collector.
- Tesseract runs locally; nothing is sent over the network.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

from collector.ocr.engine import OcrError

TIMEOUT_SECONDS = 60
# Tesseract language codes for the user's decision "Hebrew + English".
WANTED = {"he": "heb", "en": "eng"}

_CANDIDATES = [
    os.environ.get("LEARNLOOP_TESSERACT"),                      # explicit override
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",            # default Windows installer
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def find_tesseract() -> str | None:
    for candidate in _CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which("tesseract")  # development only – to be removed when enabled for users


def _run(args: list[str], data: bytes | None = None) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no black window
    return subprocess.run(args, input=data, capture_output=True, timeout=TIMEOUT_SECONDS,
                          creationflags=flags)


class TesseractOcr:
    name = "tesseract"

    def __init__(self, languages: tuple[str, ...]):
        self.exe = find_tesseract()
        if self.exe is None:
            raise OcrError("Tesseract is not installed")
        installed = set(_run([self.exe, "--list-langs"]).stdout.decode(errors="replace").split())
        wanted = [WANTED[lang] for lang in languages if lang in WANTED]
        self.languages = [code for code in wanted if code in installed]
        self.missing = [code for code in wanted if code not in installed]
        if not self.languages:
            raise OcrError(f"Tesseract has none of the languages {wanted} (installed: {sorted(installed)})")

    def recognize(self, image) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        try:
            done = _run([self.exe, "stdin", "stdout", "-l", "+".join(self.languages)],
                        buffer.getvalue())
        except subprocess.TimeoutExpired:
            raise OcrError(f"OCR took longer than {TIMEOUT_SECONDS} seconds")
        if done.returncode != 0:
            raise OcrError("Tesseract failed: " + done.stderr.decode(errors="replace")[:200])
        return done.stdout.decode("utf-8", errors="replace")
