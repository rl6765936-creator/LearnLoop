"""§57 / Q16: the Collector's files with study text are encrypted at rest (Windows DPAPI)."""

import sys
import tempfile
import unittest
from pathlib import Path

from collector.security import at_rest, outbox
from collector.security.filter import FilterOutcome
from collector.readers.base import Segment


class XorProtector:
    """Stand-in for DPAPI in the cloud: reversible, and visibly not plain text."""
    name, header = "test", b"LLT1"

    def protect(self, data):
        return bytes(b ^ 0x5A for b in data)

    def unprotect(self, data):
        return bytes(b ^ 0x5A for b in data)


class TestAtRest(unittest.TestCase):
    def setUp(self):
        self.saved = at_rest._protector
        at_rest.set_protector(XorProtector())
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        at_rest.set_protector(self.saved)

    def test_outbox_entry_text_not_on_disk(self):
        outcome = FilterOutcome("clean", [Segment("lines 1-2", "SECRET_STUDY_TEXT רשימות")])
        outbox.write_entry(self.dir, "a/x.py", "sha", outcome, ("/f", "g1"))
        raw = b"".join(p.read_bytes() for p in self.dir.glob("*.json"))
        self.assertNotIn(b"SECRET_STUDY_TEXT", raw)
        self.assertTrue(raw.startswith(b"LLT1"))
        self.assertEqual(outbox.list_entries(self.dir)[0]["segments"][0]["text"], "SECRET_STUDY_TEXT רשימות")

    def test_old_plain_files_still_readable(self):
        p = self.dir / "old.json"
        p.write_text('{"source": "x"}', encoding="utf-8")
        self.assertEqual(at_rest.read(p), {"source": "x"})

    def test_manual_text_encrypted(self):
        from collector.security import review
        p = self.dir / "manual.json"
        review.save_manual("sha1", "TYPED_BY_HER", p)
        self.assertNotIn(b"TYPED_BY_HER", p.read_bytes())
        self.assertEqual(review.load_manual(p), {"sha1": "TYPED_BY_HER"})

    def test_foreign_file_refused(self):
        p = self.dir / "x.json"
        p.write_bytes(b"LLZ9garbage")
        with self.assertRaises(at_rest.AtRestError):
            at_rest.read(p)


@unittest.skipUnless(sys.platform == "win32", "real Windows DPAPI – runs on her computer only")
class TestRealDpapi(unittest.TestCase):
    def test_round_trip(self):
        p = at_rest.DpapiProtector()
        blob = p.protect("שלום".encode("utf-8"))
        self.assertNotIn("שלום".encode("utf-8"), blob)
        self.assertEqual(p.unprotect(blob).decode("utf-8"), "שלום")


if __name__ == "__main__":
    unittest.main()
