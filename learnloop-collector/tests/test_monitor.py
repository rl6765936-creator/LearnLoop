"""Tests for change detection (Stage 5). Synthetic files in temporary folders only."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collector.config import CollectorConfig, load_config, save_config
from collector.monitor import monitor as monitor_module
from collector.monitor.monitor import MAX_ATTEMPTS, Monitor
from collector.monitor.state import REMOVED, load_state
from collector.permissions import folder_access
from collector.readers import base


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base_dir = Path(self._tmp.name).resolve()
        self.root = base_dir / "study"
        self.root.mkdir()
        self.outside = base_dir / "outside"
        self.outside.mkdir()
        self.cfg = base_dir / "local_data" / "settings.json"
        self.state = base_dir / "local_data" / "state.json"
        self.outbox = base_dir / "local_data" / "outbox"
        self.review = base_dir / "local_data" / "review.json"
        save_config(CollectorConfig(approved_folder=str(self.root)), self.cfg)
        self.clock = FakeClock()

    def tearDown(self):
        self._tmp.cleanup()

    def monitor(self, settle=0.0):
        # Outbox and review decisions also go to the temporary folder – never the real ones.
        return Monitor(self.cfg, self.state, settle_seconds=settle, clock=self.clock,
                       outbox_dir=self.outbox, review_path=self.review)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class TestDetection(MonitorTestCase):
    def test_first_scan_processes_all_new_files(self):
        self.write("a.txt", "alpha")
        self.write("שנה ב/סיכום.md", "בטא")
        report = self.monitor().run_once()
        self.assertEqual(sorted(report.added), ["a.txt", "שנה ב/סיכום.md"])
        self.assertTrue(all(r.status == base.OK for r in report.results))

    def test_unchanged_files_are_not_reprocessed(self):
        self.write("a.txt", "alpha")
        m = self.monitor()
        m.run_once()
        with mock.patch.object(monitor_module, "extract_file") as extract:
            report = m.run_once()
        extract.assert_not_called()
        self.assertFalse(report.changed_anything)

    def test_only_the_changed_file_is_reprocessed(self):
        self.write("a.txt", "alpha")
        self.write("b.txt", "beta")
        m = self.monitor()
        m.run_once()
        self.write("b.txt", "beta – edited, longer")
        report = m.run_once()
        self.assertEqual(report.modified, ["b.txt"])
        self.assertEqual([r.path.name for r in report.results], ["b.txt"])

    def test_date_only_change_is_not_reprocessed(self):
        path = self.write("a.txt", "alpha")
        m = self.monitor()
        m.run_once()
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        report = m.run_once()
        self.assertEqual(report.touched, ["a.txt"])
        self.assertEqual(report.results, [])

    def test_deleted_file_is_marked_removed_and_history_kept(self):
        path = self.write("a.txt", "alpha")
        m = self.monitor()
        m.run_once()
        path.unlink()
        report = m.run_once()
        self.assertEqual(report.removed, ["a.txt"])
        state, _ = load_state(self.state)
        self.assertEqual(state.files["a.txt"].status, REMOVED)

    def test_moved_out_of_folder_counts_as_removed(self):
        path = self.write("a.txt", "alpha")
        m = self.monitor()
        m.run_once()
        path.rename(self.outside / "a.txt")
        self.assertEqual(m.run_once().removed, ["a.txt"])

    def test_rename_inside_folder_is_a_move_not_reprocessing(self):
        path = self.write("a.txt", "alpha content")
        m = self.monitor()
        m.run_once()
        (self.root / "sub").mkdir()
        path.rename(self.root / "sub" / "renamed.txt")
        report = m.run_once()
        self.assertEqual(report.moved, [("a.txt", "sub/renamed.txt")])
        self.assertEqual(report.results, [])
        self.assertEqual(report.removed, [])

    def test_deleted_file_that_returns_is_new_again(self):
        path = self.write("a.txt", "alpha")
        m = self.monitor()
        m.run_once()
        path.unlink()
        m.run_once()
        self.write("a.txt", "alpha again")
        self.assertEqual(m.run_once().added, ["a.txt"])

    def test_exact_duplicates_are_reported(self):
        self.write("a.txt", "same")
        self.write("copy/a (1).txt", "same")
        report = self.monitor().run_once()
        self.assertEqual(report.duplicates, [["a.txt", "copy/a (1).txt"]])

    def test_empty_files_are_not_reported_as_duplicates(self):
        self.write("a.txt", "")
        self.write("b.pdf", "")
        self.assertEqual(self.monitor().run_once().duplicates, [])

    def test_office_lock_files_are_ignored(self):
        self.write("~$test.docx", "lock")
        self.write("report.tmp", "temp")
        self.write("ok.txt", "fine")
        report = self.monitor().run_once()
        self.assertEqual(report.added, ["ok.txt"])
        self.assertEqual(report.persistent_failures, [])

    def test_excluded_files_and_folders_are_never_read(self):
        self.write(".env", "PASSWORD=1")
        self.write("venv/lib.py", "x = 1")
        self.write("ok.txt", "fine")
        report = self.monitor().run_once()
        self.assertEqual(report.added, ["ok.txt"])
        blocked = [rel for rel, _ in report.blocked]
        self.assertIn(".env", blocked)
        self.assertIn("venv", blocked)
        self.assertNotIn("venv/lib.py", blocked)  # we never looked inside


class TestSettleAndPermissions(MonitorTestCase):
    def test_file_waits_until_stable(self):
        self.write("a.txt", "part 1")
        m = self.monitor(settle=2.0)
        first = m.run_once()
        self.assertEqual(first.pending, ["a.txt"])
        self.assertEqual(first.added, [])
        self.clock.now += 1
        self.write("a.txt", "part 1 + part 2")   # still being written -> timer restarts
        self.assertEqual(m.run_once().pending, ["a.txt"])
        self.clock.now += 3
        self.assertEqual(m.run_once().added, ["a.txt"])

    def test_rename_is_a_move_even_while_new_name_is_settling(self):
        # Found on Windows: old name vanished at once, new name was still "waiting".
        path = self.write("a.docx.txt", "content of a")
        m = self.monitor(settle=2.0)
        m.run_once()
        self.clock.now += 3
        m.run_once()                       # a.docx.txt is now known
        path.rename(self.root / "b.txt")
        first = m.run_once()
        self.assertEqual(first.pending, ["b.txt"])
        self.assertEqual(first.removed, [])  # not removed yet – might be a rename
        self.clock.now += 3
        second = m.run_once()
        self.assertEqual(second.moved, [("a.docx.txt", "b.txt")])
        self.assertEqual(second.removed, [])
        self.assertEqual(second.results, [])

    def test_real_delete_is_reported_after_pending_files_settle(self):
        gone = self.write("gone.txt", "bye")
        m = self.monitor(settle=2.0)
        m.run_once()
        self.clock.now += 3
        m.run_once()
        gone.unlink()
        self.write("other.txt", "different content")
        self.assertEqual(m.run_once().removed, [])
        self.clock.now += 3
        report = m.run_once()
        self.assertEqual(report.removed, ["gone.txt"])
        self.assertEqual(report.added, ["other.txt"])

    def test_paused_reads_nothing(self):
        self.write("a.txt", "alpha")
        folder_access.set_paused(True, self.cfg)
        with mock.patch.object(monitor_module, "extract_file") as extract:
            report = self.monitor().run_once()
        extract.assert_not_called()
        self.assertIn("paused", report.status)

    def test_no_folder_reads_nothing(self):
        folder_access.revoke_folder(self.cfg)
        self.assertIn("no folder", self.monitor().run_once().status)

    def test_revoke_during_cycle_stops_immediately(self):
        for name in ("a.txt", "b.txt", "c.txt"):
            self.write(name, name)
        real_extract = monitor_module.extract_file

        def extract_then_revoke(path, guard, **kwargs):
            result = real_extract(path, guard, **kwargs)
            folder_access.revoke_folder(self.cfg)   # user revokes while we work
            return result

        with mock.patch.object(monitor_module, "extract_file", side_effect=extract_then_revoke):
            report = self.monitor().run_once()
        # Hardening 2026-11-24 (finding 1): the file that was being read when the user
        # revoked is DISCARDED too – before, its result was kept and reached the outbox.
        self.assertEqual(report.results, [])
        self.assertIn("stopped", report.status)
        self.assertFalse(self.outbox.exists() and any(self.outbox.glob("*.json")))

    def test_new_folder_starts_fresh(self):
        self.write("a.txt", "alpha")
        m = self.monitor()
        m.run_once()
        other = self.root.parent / "study_b"
        other.mkdir()
        (other / "b.txt").write_text("beta", encoding="utf-8")
        config = load_config(self.cfg).config
        config.approved_folder = str(other)
        save_config(config, self.cfg)
        report = m.run_once()
        self.assertEqual(report.added, ["b.txt"])
        self.assertTrue(any("starting fresh" in n for n in report.notes))


class TestRetriesAndState(MonitorTestCase):
    def test_failed_file_is_retried_then_reported_once(self):
        self.write("fake.pdf", "not really a pdf")
        m = self.monitor()
        reports = [m.run_once() for _ in range(MAX_ATTEMPTS + 2)]
        self.assertEqual(reports[0].added, ["fake.pdf"])
        self.assertEqual(reports[1].retried, ["fake.pdf"])
        flagged = [r for r in reports if r.persistent_failures]
        self.assertEqual(len(flagged), 1)                 # reported exactly once
        self.assertEqual(reports[-1].results, [])         # no endless retrying

    def test_fixed_file_after_failure_is_processed(self):
        self.write("x.txt", "")
        bad = self.root / "x.txt"
        bad.write_bytes(b"\x00\x01binary")
        m = self.monitor()
        self.assertEqual(m.run_once().results[0].status, base.FAILED)
        self.write("x.txt", "now proper text")
        report = m.run_once()
        self.assertEqual(report.results[0].status, base.OK)

    def test_state_survives_restart(self):
        self.write("a.txt", "alpha")
        self.monitor().run_once()
        with mock.patch.object(monitor_module, "extract_file") as extract:
            self.monitor().run_once()   # a brand-new Monitor = program restarted
        extract.assert_not_called()

    def test_corrupted_state_file_starts_fresh(self):
        self.write("a.txt", "alpha")
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text("{ broken", encoding="utf-8")
        report = self.monitor().run_once()
        self.assertEqual(report.added, ["a.txt"])
        self.assertTrue(any("starting fresh" in n for n in report.notes))

    def test_state_does_not_store_file_content(self):
        self.write("a.txt", "TOP-SECRET-CONTENT")
        self.monitor().run_once()
        self.assertNotIn(b"TOP-SECRET-CONTENT", self.state.read_bytes())   # encrypted on Windows anyway


if __name__ == "__main__":
    unittest.main()
