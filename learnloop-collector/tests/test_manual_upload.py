"""Manual upload (Q13): one-time, same filter and rules, fail closed."""

import tempfile
import unittest
from pathlib import Path

from collector.sources.manual_upload import prepare

KEY = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8t"


class TestManualUpload(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.home = self.base / "home"
        (self.home / "Downloads").mkdir(parents=True)
        (self.base / "docs").mkdir()

    def test_secret_removed_and_one_time_grant(self):
        f = self.base / "docs" / "notes.py"
        f.write_text(f'KEY = "{KEY}"\nnums = [3, 1]\n', encoding="utf-8")
        envs, msg = prepare(str(f), home=self.home, ocr=None)
        self.assertIsNotNone(envs, msg)
        text = "".join(c["text"] for e in envs for c in e["chunks"])
        self.assertNotIn(KEY, text)
        self.assertEqual(envs[0]["source"]["type"], "manual")
        envs2, _ = prepare(str(f), home=self.home, ocr=None)
        self.assertNotEqual(envs[0]["source"]["grant_id"], envs2[0]["source"]["grant_id"])   # never reused

    def test_forbidden_and_excluded(self):
        d = self.home / "Downloads" / "a.txt"
        d.write_text("hello", encoding="utf-8")
        self.assertIsNone(prepare(str(d), home=self.home, ocr=None)[0])
        env = self.base / "docs" / ".env"
        env.write_text("TOKEN=abc", encoding="utf-8")
        self.assertIsNone(prepare(str(env), home=self.home, ocr=None)[0])
        self.assertIsNone(prepare("relative.txt", home=self.home, ocr=None)[0])


if __name__ == "__main__":
    unittest.main()
