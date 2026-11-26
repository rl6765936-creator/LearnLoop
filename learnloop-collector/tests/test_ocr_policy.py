"""Decision 2026-11-24 (A): text that comes ONLY from OCR is not used – the file goes to the
unreadable list (ignore / type its content). Files with a text layer are not affected."""

import tempfile
import unittest
from pathlib import Path

from collector.config import CollectorConfig
from collector.permissions.path_guard import PathGuard
from collector.readers import extract


class EngineThatMustNotRun:
    name, languages = "fake", ("en",)

    def recognize(self, image):
        raise AssertionError("OCR must not run under decision A")


class TestOcrPolicy(unittest.TestCase):
    def setUp(self):
        self.assertFalse(extract.USE_OCR_TEXT)
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.guard = PathGuard(CollectorConfig(approved_folder=str(self.root)))

    def test_image_goes_to_unreadable_list(self):
        from PIL import Image
        img = self.root / "scan.png"
        Image.new("RGB", (50, 50), "white").save(img)
        result = extract.extract_file(img, self.guard, ocr=EngineThatMustNotRun())
        self.assertEqual(result.status, "failed")
        self.assertIn("decision 24.11", result.error)

    def test_text_files_unaffected(self):
        f = self.root / "a.txt"
        f.write_text("שלום", encoding="utf-8")
        self.assertEqual(extract.extract_file(f, self.guard, ocr=EngineThatMustNotRun()).status, "ok")


if __name__ == "__main__":
    unittest.main()


class TestNoUselessRetries(unittest.TestCase):
    def test_image_goes_to_unreadable_list_at_once(self):
        import shutil
        from PIL import Image
        from collector.config import save_config
        from collector.monitor.monitor import Monitor
        from collector.permissions import folder_access
        base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, base, True)
        root = base / "study"
        root.mkdir()
        Image.new("RGB", (40, 40), "white").save(root / "scan.png")
        cfg = base / "c.json"
        self.assertTrue(folder_access.grant_folder(str(root), cfg, home=base / "nohome").ok)
        m = Monitor(cfg, base / "s.json", settle_seconds=0, outbox_dir=base / "o", review_path=base / "r.json",
                    manual_path=base / "m.json")
        report = m.run_once()
        self.assertEqual([r for r, _ in report.persistent_failures], ["scan.png"])
