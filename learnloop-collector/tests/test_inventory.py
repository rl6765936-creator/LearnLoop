"""Inventory: counts the approved folder locally. Read-only, aggregate-only. Synthetic files."""

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector import inventory
from collector.config import CollectorConfig, load_config, save_config
from collector.inventory import build_inventory, format_report
from collector.transfer.envelope import MAX_CHUNK_CHARS, MAX_CHUNKS_PER_ENVELOPE, build_envelopes

RANDOM_TOKEN = "Zx8Qp2Lm7Rt4Vb9Nc1Hy6Wk3Js5Df0GaK2pT9"
FAKE_OPENAI = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
NAMES = ["שברים-שיעור1.txt", "qloops.py", "qcopyloops.py", "qnotes.md", "qheldfile.txt", "qkeyfile.py",
         "qscan.png", "qbig.txt"]


class TestInventory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.base, self.root = base, base / "study"
        self.root.mkdir()
        self.cfg = base / "local_data" / "settings.json"
        save_config(CollectorConfig(approved_folder=str(self.root)), self.cfg)
        w = lambda n, t: (self.root / n).write_text(t, encoding="utf-8")
        w(NAMES[0], "שברים: מכנה משותף וחיבור שברים. " * 20)
        w(NAMES[1], "for i in range(3):\n    print(i)\n")
        w(NAMES[2], "for i in range(3):\n    print(i)\n")              # identical duplicate
        w(NAMES[3], "# Lists\nA list keeps order.\n")
        w(NAMES[4], f"x {RANDOM_TOKEN}")                                  # uncertain → held
        w(NAMES[5], f'KEY = "{FAKE_OPENAI}"\nprint(1)\n')                 # definite → redacted
        (self.root / NAMES[6]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        w(NAMES[7], "a" * (MAX_CHUNK_CHARS * (MAX_CHUNKS_PER_ENVELOPE + 1)))  # needs 2 envelopes
        w(".env", "PASSWORD=abc")                                         # built-in exclusion
        self.before = sorted(p.name for p in self.base.rglob("*"))

    def tearDown(self):
        self._tmp.cleanup()

    def run_inv(self, **kw):
        return build_inventory(config_path=self.cfg, now=None, **kw)

    def test_counts(self):
        r = self.run_inv()
        c = r["counts"]
        self.assertEqual(c["duplicates"], 1)
        self.assertEqual(c["held_for_review"], 1)
        self.assertEqual(c["redacted_files"], 1)
        self.assertEqual(c["needs_ocr_files"], 1)
        self.assertGreaterEqual(c["excluded_or_blocked"], 1)
        self.assertEqual(c["multi_envelope_files"], 1)
        self.assertGreater(r["total"]["hebrew_chars"], 0)
        e = r["estimate"]
        self.assertLess(e["content_tokens"]["low"], e["content_tokens"]["mid"])
        self.assertLess(e["content_tokens"]["mid"], e["content_tokens"]["high"])
        self.assertEqual(e["requests"], r["total"]["envelopes"])

    def test_envelope_count_matches_the_real_transfer_chunking(self):
        r = self.run_inv()
        text = (self.root / "qbig.txt").read_text(encoding="utf-8")
        entry = {"source": "x", "sha256": "0" * 64, "segments": [{"location": "lines 1-1", "text": text}]}
        expected = len(build_envelopes(entry, "folder-x", "g", "new"))
        self.assertEqual(expected, 2)
        # text files: the big one (2 envelopes) + the Hebrew file + the .md file (1 each)
        self.assertEqual(r["by_type"]["text"]["envelopes"], expected + 2)

    def test_read_only_and_no_names_or_content(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")):
            r = self.run_inv(prices={"example": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}})
        self.assertEqual(sorted(p.name for p in self.base.rglob("*")), self.before)   # nothing written
        blob = json.dumps(r, ensure_ascii=False) + format_report(r)
        for name in NAMES:
            self.assertNotIn(Path(name).stem, blob)
        for content in ("מכנה משותף", "print(i)", RANDOM_TOKEN, FAKE_OPENAI, "A list keeps order"):
            self.assertNotIn(content, blob)
        self.assertIn("example", r["estimate"]["cost_usd"])
        self.assertIn("nothing was sent", format_report(r))

    def test_no_folder_or_paused_reads_nothing(self):
        save_config(CollectorConfig(), self.cfg)
        self.assertIn("error", self.run_inv())
        save_config(CollectorConfig(approved_folder=str(self.root), paused=True), self.cfg)
        self.assertIn("error", self.run_inv())

    def test_revoke_during_inventory_stops_it(self):
        real_scan = inventory.scan_folder

        def scan_then_revoke(root, guard):
            result = real_scan(root, guard)
            save_config(CollectorConfig(), self.cfg)          # user revokes while we run
            return result
        with mock.patch.object(inventory, "scan_folder", scan_then_revoke):
            r = self.run_inv()
        self.assertIsNotNone(r["counts"]["stopped"])
        self.assertEqual(r["total"]["files"], 0)


if __name__ == "__main__":
    unittest.main()
