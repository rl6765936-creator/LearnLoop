"""Tests for Stage 6 (OCR) with a FAKE engine, so they run on any computer.

The real Windows engine is checked manually on Windows (ocr-languages + read).
"""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector.config import CollectorConfig, save_config
from collector.monitor.monitor import IGNORED, Monitor
from collector.ocr import images
from collector.permissions.path_guard import PathGuard
from collector.readers import base
from collector.readers.extract import extract_file
from collector.security import outbox
from collector.security.review import IGNORE, save_decision, save_manual


class FakeOcr:
    name = "fake"

    def __init__(self, text="טקסט מזוהה OCR text"):
        self.text = text
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        return self.text


class BrokenOcr:
    name = "broken"

    def recognize(self, image):
        raise RuntimeError("engine crashed")


def png_bytes(size=(200, 100)) -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def scanned_pdf_bytes(pages=1) -> bytes:
    """A PDF made only of images – no text layer, like a scanner produces."""
    from PIL import Image
    frames = [Image.new("RGB", (300, 400), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="PDF", save_all=True, append_images=frames[1:])
    return buffer.getvalue()


class OcrTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "study"
        self.root.mkdir()
        self.guard = PathGuard(CollectorConfig(approved_folder=str(self.root)))

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path


class TestImageOcr(OcrTestCase):
    def test_image_is_read_with_ocr(self):
        self.write("whiteboard.png", png_bytes())
        r = extract_file(self.root / "whiteboard.png", self.guard, ocr=FakeOcr())
        self.assertEqual(r.status, base.OK)
        self.assertEqual(r.segments[0].location, "image (OCR)")
        self.assertIn("טקסט מזוהה", r.text)

    def test_no_text_found_is_a_failure(self):
        self.write("blank.png", png_bytes())
        r = extract_file(self.root / "blank.png", self.guard, ocr=FakeOcr(text="   "))
        self.assertEqual(r.status, base.FAILED)
        self.assertIn("no text", r.error)

    def test_fake_image_is_detected(self):
        self.write("fake.jpg", b"this is text, not a picture")
        r = extract_file(self.root / "fake.jpg", self.guard, ocr=FakeOcr())
        self.assertIn("misleading", r.error)

    def test_huge_image_is_refused_before_decoding(self):
        self.write("huge.png", png_bytes((1000, 1000)))
        engine = FakeOcr()
        with mock.patch.object(images, "MAX_IMAGE_PIXELS", 500_000):
            r = extract_file(self.root / "huge.png", self.guard, ocr=engine)
        self.assertEqual(r.status, base.FAILED)
        self.assertEqual(engine.calls, 0)

    def test_engine_crash_does_not_crash_the_collector(self):
        self.write("a.png", png_bytes())
        r = extract_file(self.root / "a.png", self.guard, ocr=BrokenOcr())
        self.assertEqual(r.status, base.FAILED)
        self.assertIn("OCR engine failed", r.error)


class TestScannedPdf(OcrTestCase):
    def test_scanned_pdf_pages_are_ocred(self):
        self.write("scan.pdf", scanned_pdf_bytes(pages=2))
        engine = FakeOcr()
        r = extract_file(self.root / "scan.pdf", self.guard, ocr=engine)
        self.assertEqual(r.status, base.OK, r.error)
        self.assertEqual([s.location for s in r.segments], ["page 1 (OCR)", "page 2 (OCR)"])
        self.assertEqual(engine.calls, 2)

    def test_mixed_pdf_only_ocrs_pages_without_text(self):
        from pypdf import PdfReader, PdfWriter
        from test_readers import make_text_pdf
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(make_text_pdf("Typed page"))))
        writer.append(PdfReader(io.BytesIO(scanned_pdf_bytes(pages=1))))
        buffer = io.BytesIO()
        writer.write(buffer)
        self.write("mixed.pdf", buffer.getvalue())
        engine = FakeOcr()
        r = extract_file(self.root / "mixed.pdf", self.guard, ocr=engine)
        self.assertEqual([s.location for s in r.segments], ["page 1", "page 2 (OCR)"])
        self.assertEqual(engine.calls, 1)

    def test_page_limit(self):
        self.write("long.pdf", scanned_pdf_bytes(pages=4))
        engine = FakeOcr()
        with mock.patch.object(images, "MAX_OCR_PAGES", 2):
            r = extract_file(self.root / "long.pdf", self.guard, ocr=engine)
        self.assertEqual(engine.calls, 2)
        self.assertTrue(any("only the first 2" in w for w in r.warnings))


class TestManualFallback(OcrTestCase):
    """Spec §5: unreadable file -> user types the content, or ignores the file."""

    def setUp(self):
        super().setUp()
        self.cfg = self.base / "settings.json"
        self.manual = self.base / "manual.json"
        self.review = self.base / "review.json"
        self.outbox = self.base / "outbox"
        save_config(CollectorConfig(approved_folder=str(self.root)), self.cfg)
        self.write("photo.png", png_bytes())
        self.patch = mock.patch("collector.ocr.engine.get_engine", return_value=None)
        self.patch.start()
        self.monitor = Monitor(self.cfg, self.base / "state.json", settle_seconds=0,
                               outbox_dir=self.outbox, review_path=self.review,
                               manual_path=self.manual)

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def test_typed_text_is_filtered_and_sent(self):
        self.monitor.run_once()
        sha = self.monitor.state.files["photo.png"].sha256
        save_manual(sha, "Graphs lecture, password = Hunter2!x", self.manual)
        report = self.monitor.run_once()
        self.assertEqual(report.released, ["photo.png"])
        entry = outbox.list_entries(self.outbox)[0]
        text = entry["segments"][0]["text"]
        self.assertIn("Graphs lecture", text)
        self.assertNotIn("Hunter2!x", text)   # typed text also passes the filter
        self.assertEqual(entry["segments"][0]["location"], "typed by user")

    def test_ignored_unreadable_file_stops_retrying(self):
        self.monitor.run_once()
        save_decision(self.monitor.state.files["photo.png"].sha256, IGNORE, self.review)
        self.monitor.run_once()
        self.assertEqual(self.monitor.state.files["photo.png"].status, IGNORED)
        later = self.monitor.run_once()
        self.assertEqual(later.results, [])
        self.assertEqual(later.persistent_failures, [])


if __name__ == "__main__":
    unittest.main()


class TestRealTesseract(unittest.TestCase):
    """Runs only where Tesseract is installed. Uses English so it also works on machines
    without the Hebrew language file; Hebrew is checked manually on Windows."""

    def setUp(self):
        from collector.ocr.engine import OcrError
        from collector.ocr.tesseract_ocr import TesseractOcr
        try:
            self.engine = TesseractOcr(("en",))
        except (OcrError, OSError):
            self.skipTest("Tesseract is not installed on this computer")

    def test_reads_printed_english(self):
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (900, 160), "white")
        font = ImageFont.load_default(size=56)
        ImageDraw.Draw(image).text((20, 40), "BINARY SEARCH TREE", fill="black", font=font)
        text = self.engine.recognize(image)
        self.assertIn("BINARY", text.upper())
        self.assertIn("TREE", text.upper())


# These tests check the OCR machinery itself, which is kept for a future decision. Decision
# 2026-11-24 (A) switches the USE of OCR text off; that is tested in test_ocr_policy.py.
def setUpModule():
    from collector.readers import extract
    extract._saved_use_ocr, extract.USE_OCR_TEXT = extract.USE_OCR_TEXT, True


def tearDownModule():
    from collector.readers import extract
    extract.USE_OCR_TEXT = extract._saved_use_ocr
