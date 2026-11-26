"""Regression tests for the Collector hardening of 2026-11-24.

Finding 1 – revoke / pause while a file is being read must discard that file too.
Finding 2 – exclusion of a file whose name contains [ ] must work.
Q3 (A)    – outbox content from a revoked / replaced approval is isolated, kept,
            and can never pass the transfer gate.
Finding 4 – English-only OCR is made visible as a warning (OCR decision unchanged).

Synthetic files in temporary folders only.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path, PurePath
from unittest import mock

from collector.config import CollectorConfig, load_config, save_config
from collector.monitor import monitor as monitor_module
from collector.monitor.monitor import Monitor
from collector.permissions import folder_access
from collector.permissions.path_guard import PathGuard, matching_exclusion
from collector.readers import base
from collector.readers.extract import extract_file
from collector.security import outbox


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "study"
        self.root.mkdir()
        self.cfg = self.base / "local_data" / "settings.json"
        self.state = self.base / "local_data" / "state.json"
        self.outbox = self.base / "local_data" / "outbox"
        self.review = self.base / "local_data" / "review.json"
        self.manual = self.base / "local_data" / "manual.json"
        self.grant(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def grant(self, folder):
        result = folder_access.grant_folder(str(folder), self.cfg, home=self.base / "nohome")
        self.assertTrue(result.ok, result.message)

    def config(self):
        return load_config(self.cfg).config

    def monitor(self):
        return Monitor(self.cfg, self.state, settle_seconds=0, outbox_dir=self.outbox,
                       review_path=self.review, manual_path=self.manual)

    def write(self, rel, text, folder=None):
        path = (folder or self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def entries(self):
        return {e["source"]: e for e in outbox.list_entries(self.outbox)}

    def transferable(self):
        return {e["source"] for e in outbox.transferable_entries(self.outbox, self.config())}


# ---------------------------------------------------------------- finding 1
class TestRevokeWhileReading(Base):
    def _run_with(self, action):
        real = monitor_module.extract_file

        def extract_then(path, guard, **kwargs):
            result = real(path, guard, **kwargs)
            action()                     # user acts while the file is being read
            return result

        with mock.patch.object(monitor_module, "extract_file", side_effect=extract_then):
            return self.monitor().run_once()

    def test_revoke_while_reading_writes_nothing(self):
        self.write("a.txt", "lesson one")
        report = self._run_with(lambda: folder_access.revoke_folder(self.cfg))
        self.assertIn("stopped", report.status)
        self.assertEqual(report.added, [])
        self.assertEqual(self.entries(), {})

    def test_pause_while_reading_writes_nothing_and_resume_processes_it(self):
        self.write("a.txt", "lesson one")
        report = self._run_with(lambda: folder_access.set_paused(True, self.cfg))
        self.assertIn("stopped", report.status)
        self.assertEqual(self.entries(), {})
        folder_access.set_paused(False, self.cfg)
        report = self.monitor().run_once()
        self.assertEqual(report.added, ["a.txt"])
        self.assertEqual(self.transferable(), {"a.txt"})

    def test_revoke_just_before_outbox_write_writes_nothing(self):
        self.write("a.txt", "lesson one")
        real_filter = monitor_module.filter_result

        def filter_then_revoke(*args, **kwargs):
            outcome = real_filter(*args, **kwargs)
            folder_access.revoke_folder(self.cfg)
            return outcome

        with mock.patch.object(monitor_module, "filter_result", side_effect=filter_then_revoke):
            report = self.monitor().run_once()
        self.assertIn("stopped", report.status)
        self.assertEqual(self.entries(), {})

    def test_long_ocr_stops_between_pages(self):
        class CountingOcr:
            languages = ["en", "he"]

            def __init__(self):
                self.calls = 0

            def recognize(self, image):
                self.calls += 1
                return f"page text {self.calls}"

        from PIL import Image
        pages = [Image.new("RGB", (50, 50), "white") for _ in range(3)]
        buffer = io.BytesIO()
        pages[0].save(buffer, format="PDF", save_all=True, append_images=pages[1:])
        (self.root / "scan.pdf").write_bytes(buffer.getvalue())

        engine = CountingOcr()
        answers = iter([True, False, False, False])   # revoked after the first page
        result = extract_file(self.root / "scan.pdf", PathGuard(self.config()), ocr=engine,
                              still_allowed=lambda: next(answers))
        self.assertEqual(result.status, base.BLOCKED)
        self.assertEqual(engine.calls, 1)
        self.assertEqual(result.segments, [])


# ---------------------------------------------------------------- finding 2
class TestBracketExclusions(Base):
    def test_literal_names_with_brackets_exclude_themselves(self):
        for name in ["notes[1].txt", "סיכום [טיוטה].docx", "[draft] plan.md"]:
            self.assertIsNotNone(matching_exclusion(PurePath(name), [name]), name)

    def test_literal_folder_with_brackets_excludes_its_content(self):
        self.assertIsNotNone(matching_exclusion(PurePath("old[2]/a.txt"), ["old[2]"]))

    def test_literal_match_ignores_case_and_backslashes(self):
        self.assertIsNotNone(matching_exclusion(PurePath("Sub[A]/Notes[1].TXT"),
                                                ["sub[a]\\notes[1].txt"]))

    def test_wildcards_still_work(self):
        self.assertIsNotNone(matching_exclusion(PurePath("keys/server.key"), ["*.key"]))
        self.assertIsNotNone(matching_exclusion(PurePath("notes1.txt"), ["notes[1].txt"]))
        self.assertIsNone(matching_exclusion(PurePath("notes2.txt"), ["notes[1].txt"]))

    def test_excluded_bracket_file_leaves_outbox_and_is_not_read(self):
        self.write("notes[1].txt", "draft")
        self.write("other.txt", "kept")
        self.monitor().run_once()
        self.assertIn("notes[1].txt", self.entries())
        self.assertTrue(folder_access.add_exclusion("notes[1].txt", self.cfg).ok)
        self.assertFalse(PathGuard(self.config()).check(self.root / "notes[1].txt").allowed)
        # Even before the next scan, the gate refuses it.
        self.assertEqual(self.transferable(), {"other.txt"})
        with mock.patch.object(monitor_module, "extract_file") as extract:
            self.monitor().run_once()
        extract.assert_not_called()
        self.assertEqual(set(self.entries()), {"other.txt"})


# ---------------------------------------------------------------- Q3 option A
class TestOutboxAfterRevoke(Base):
    def collect(self, *names):
        for name in names:
            self.write(name, f"content of {name}")
        self.monitor().run_once()

    def test_entries_record_their_approval(self):
        self.collect("a.txt")
        grant = self.entries()["a.txt"]["grant"]
        self.assertEqual((grant["folder"], grant["grant_id"]), self.config().grant_key())
        self.assertEqual(self.transferable(), {"a.txt"})

    def test_revoke_isolates_and_blocks(self):
        self.collect("a.txt")
        folder_access.revoke_folder(self.cfg)
        report = self.monitor().run_once()
        self.assertTrue(any("isolated" in n for n in report.notes))
        self.assertEqual(self.entries(), {})
        isolated = outbox.list_quarantine(self.outbox)
        self.assertEqual([e["source"] for e in isolated], ["a.txt"])
        self.assertIs(isolated[0]["transfer_allowed"], False)
        self.assertEqual(isolated[0]["quarantine_reason"], "permission revoked")
        self.assertIn("content of a.txt", json.dumps(isolated[0], ensure_ascii=False))  # kept

    def test_gate_blocks_everything_after_revoke_even_before_isolation(self):
        self.collect("a.txt")
        entry = self.entries()["a.txt"]
        folder_access.revoke_folder(self.cfg)
        self.assertEqual(outbox.transfer_gate(entry, self.config()),
                         (False, "no folder is approved"))

    def test_regrant_same_folder_does_not_revive_old_content(self):
        self.collect("a.txt")
        old_entry = self.entries()["a.txt"]
        folder_access.revoke_folder(self.cfg)
        self.monitor().run_once()
        self.grant(self.root)                            # approve the SAME folder again
        self.monitor().run_once()
        # The file is collected again under the new approval...
        self.assertEqual(self.transferable(), {"a.txt"})
        self.assertNotEqual(self.entries()["a.txt"]["grant"], old_entry["grant"])
        # ...and the old copy is still isolated and still blocked.
        isolated = outbox.list_quarantine(self.outbox)
        self.assertEqual(len(isolated), 1)
        self.assertFalse(outbox.transfer_gate(isolated[0], self.config())[0])
        self.assertFalse(outbox.transfer_gate(old_entry, self.config())[0])

    def test_folder_change_cannot_carry_old_content(self):
        self.collect("a.txt")
        old_entry = self.entries()["a.txt"]
        other = self.base / "study_b"
        other.mkdir()
        self.write("a.txt", "content of b-folder", folder=other)
        self.grant(other)
        self.assertFalse(outbox.transfer_gate(old_entry, self.config())[0])
        self.monitor().run_once()
        self.assertEqual(self.transferable(), {"a.txt"})
        self.assertIn("b-folder", json.dumps(self.entries()["a.txt"], ensure_ascii=False))
        self.assertEqual(outbox.list_quarantine(self.outbox)[0]["quarantine_reason"],
                         "approved folder or approval changed")

    def test_pause_blocks_transfer_without_isolating(self):
        self.collect("a.txt")
        folder_access.set_paused(True, self.cfg)
        self.monitor().run_once()
        self.assertIn("a.txt", self.entries())
        self.assertEqual(outbox.list_quarantine(self.outbox), [])
        self.assertEqual(self.transferable(), set())
        folder_access.set_paused(False, self.cfg)
        self.assertEqual(self.transferable(), {"a.txt"})

    def test_quarantined_copy_put_back_is_still_blocked(self):
        self.collect("a.txt")
        entry = dict(self.entries()["a.txt"], transfer_allowed=False)
        self.assertFalse(outbox.transfer_gate(entry, self.config())[0])

    def test_entry_without_or_with_forged_grant_is_blocked(self):
        self.collect("a.txt")
        entry = self.entries()["a.txt"]
        no_grant = {k: v for k, v in entry.items() if k != "grant"}
        forged = dict(entry, grant={"folder": entry["grant"]["folder"], "grant_id": "x" * 32})
        self.assertFalse(outbox.transfer_gate(no_grant, self.config())[0])
        self.assertFalse(outbox.transfer_gate(forged, self.config())[0])

    def test_nothing_is_ever_deleted_from_quarantine(self):
        self.collect("a.txt")
        for _ in range(3):
            folder_access.revoke_folder(self.cfg)
            self.monitor().run_once()
            self.grant(self.root)
            self.monitor().run_once()
        self.assertEqual(len(outbox.list_quarantine(self.outbox)), 3)

    def test_damaged_entry_is_moved_not_lost(self):
        self.outbox.mkdir(parents=True)
        (self.outbox / "broken.json").write_text("{not json", encoding="utf-8")
        outbox.quarantine_unauthorized(self.outbox, self.config())
        moved = list(outbox.quarantine_dir(self.outbox).glob("*.damaged"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].read_text(encoding="utf-8"), "{not json")

    def test_old_format_outbox_is_isolated_and_files_are_collected_again(self):
        # Outbox + settings + state written by the prototype BEFORE this hardening.
        save_config(CollectorConfig(approved_folder=str(self.root)), self.cfg)  # no grant id
        self.collect("a.txt")
        for p in self.outbox.glob("*.json"):
            from collector.security import at_rest; data = at_rest.read(p)
            data.pop("grant")
            p.write_text(json.dumps(data), encoding="utf-8")
        report = self.monitor().run_once()
        self.assertTrue(any("isolated" in n for n in report.notes))
        self.assertEqual(self.transferable(), {"a.txt"})          # collected again, tagged
        self.assertEqual(outbox.list_quarantine(self.outbox)[0]["quarantine_reason"],
                         "collected before approval tracking existed")


class TestGrantIdentity(Base):
    def test_same_folder_regrant_keeps_identity(self):
        first = self.config().grant_id
        self.grant(self.root)
        self.assertEqual(self.config().grant_id, first)

    def test_revoke_then_grant_gives_new_identity(self):
        first = self.config().grant_id
        folder_access.revoke_folder(self.cfg)
        self.assertIsNone(self.config().grant_id)
        self.grant(self.root)
        self.assertNotEqual(self.config().grant_id, first)

    def test_bad_grant_id_fails_closed(self):
        self.cfg.write_text(json.dumps({"approved_folder": str(self.root), "grant_id": 5}),
                            encoding="utf-8")
        result = load_config(self.cfg)
        self.assertIsNone(result.config.approved_folder)
        self.assertIsNotNone(result.warning)

    def test_grant_id_without_folder_is_ignored(self):
        self.cfg.write_text(json.dumps({"approved_folder": None, "grant_id": "abc"}),
                            encoding="utf-8")
        self.assertIsNone(load_config(self.cfg).config.grant_key())


# ---------------------------------------------------------------- finding 4
class TestOcrLanguageWarning(Base):
    def _image(self, name):
        from PIL import Image
        Image.new("RGB", (40, 40), "white").save(self.root / name)

    def _engine(self, languages):
        class Engine:
            name = "fake"

            def recognize(self, image):
                return "Mxw Ilq zoT"   # what garbled output might look like
        engine = Engine()
        if languages is not None:
            engine.languages = languages
        return engine

    def test_english_only_engine_adds_warning(self):
        self._image("board.png")
        result = extract_file(self.root / "board.png", PathGuard(self.config()),
                              ocr=self._engine(["en"]))
        self.assertEqual(result.status, base.OK)
        self.assertTrue(any("he" in w and "garbled" in w for w in result.warnings))

    def test_engine_with_all_languages_adds_no_warning(self):
        self._image("board.png")
        result = extract_file(self.root / "board.png", PathGuard(self.config()),
                              ocr=self._engine(["he", "en-US"]))
        self.assertEqual(result.warnings, [])

    def test_engine_without_language_info_adds_no_warning(self):
        self._image("board.png")
        result = extract_file(self.root / "board.png", PathGuard(self.config()),
                              ocr=self._engine(None))
        self.assertEqual(result.warnings, [])

    def test_warning_reaches_the_outbox_entry(self):
        self._image("board.png")
        engine = self._engine(["en"])
        with mock.patch("collector.ocr.engine.get_engine", return_value=engine):
            self.monitor().run_once()
        entry = self.entries()["board.png"]
        self.assertEqual(entry["reader"], "image")
        self.assertTrue(any("garbled" in w for w in entry["warnings"]))


if __name__ == "__main__":
    unittest.main()


# These tests check the OCR machinery itself, which is kept for a future decision. Decision
# 2026-11-24 (A) switches the USE of OCR text off; that is tested in test_ocr_policy.py.
def setUpModule():
    from collector.readers import extract
    extract._saved_use_ocr, extract.USE_OCR_TEXT = extract.USE_OCR_TEXT, True


def tearDownModule():
    from collector.readers import extract
    extract.USE_OCR_TEXT = extract._saved_use_ocr
