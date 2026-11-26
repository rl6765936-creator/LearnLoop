"""Explicit classification of folders, live/backfill queues, daily budget, progress.
Synthetic files only. Decision 2026-11-24."""

import json

from collector.security import at_rest
import os
import tempfile
import time
import unittest
from pathlib import Path

from collector import collections as col
from collector.config import load_config
from collector.monitor.monitor import Monitor
from collector.permissions import folder_access
from collector.transfer.client import TransferClient
from collector.transfer.transports import CallbackTransport

YEAR_A = "שנה א'"


class Receiver:
    def __init__(self):
        self.got = []

    def __call__(self, data):
        self.got.append(json.loads(data.decode("utf-8")))
        return {"accepted": True}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.base, self.root = base, base / "study"
        (self.root / YEAR_A / "C#").mkdir(parents=True)
        (self.root / "C#").mkdir()
        (self.root / "Python").mkdir()
        self.cfg = base / "settings.json"
        self.assertTrue(folder_access.grant_folder(str(self.root), self.cfg, home=base / "nohome").ok)
        self.old = time.time() - 400 * 86400
        self.write(f"{YEAR_A}/C#/stack.md", "Stack: LIFO", old=True)
        self.write(f"{YEAR_A}/notes.txt", "סיכום שנה א", old=True)
        self.write("C#/linq.md", "LINQ queries", old=True)
        self.write("Python/lists.py", "numbers = [1, 2]", old=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text, old=False):
        p = self.root / rel
        p.write_text(text, encoding="utf-8")
        if old:
            os.utime(p, (self.old, self.old))

    def scan(self):
        m = Monitor(self.cfg, self.base / "state.json", settle_seconds=0, outbox_dir=self.base / "outbox",
                    review_path=self.base / "review.json", manual_path=self.base / "manual.json")
        m.run_once()

    def client(self, today="2026-11-24"):
        return TransferClient(CallbackTransport(self.rx), self.cfg, self.base / "outbox", self.base / "t.json",
                              monitor_state_path=self.base / "state.json", today=lambda: today)

    def allow_transfer(self):
        folder_access.set_transfer_permission(True, self.cfg)
        self.rx = Receiver()


class TestClassification(Base):
    def test_unclassified_is_unknown_and_classification_is_explicit(self):
        data = col.overview(self.cfg)
        self.assertEqual({r["collection"]: r["study_status"] for r in data["collections"]},
                         {YEAR_A: "unknown", "C#": "unknown", "Python": "unknown"})
        ok, _ = col.set_collection(YEAR_A, "completed", YEAR_A, self.cfg)
        self.assertTrue(ok)
        rows = {r["collection"]: r for r in col.overview(self.cfg)["collections"]}
        self.assertEqual((rows[YEAR_A]["study_status"], rows[YEAR_A]["period"], rows[YEAR_A]["files"]),
                         ("completed", YEAR_A, 2))
        self.assertEqual(rows["C#"]["study_status"], "unknown")        # same subject name, other folder

    def test_longest_classified_folder_wins(self):
        col.set_collection(YEAR_A, "completed", None, self.cfg)
        col.set_collection(f"{YEAR_A}/C#", "active", "חזרה", self.cfg)
        cols = load_config(self.cfg).config.active_collections()
        self.assertEqual(col.classify(f"{YEAR_A}/C#/stack.md", cols)["study_status"], "active")
        self.assertEqual(col.classify(f"{YEAR_A}/notes.txt", cols)["study_status"], "completed")
        self.assertEqual(col.classify("top.txt", cols)["collection"], col.ROOT_KEY)

    def test_only_folders_inside_the_approved_folder(self):
        outside = self.base / "elsewhere"
        outside.mkdir()
        self.assertFalse(col.set_collection(str(outside), "completed", None, self.cfg)[0])
        self.assertFalse(col.set_collection("../elsewhere", "completed", None, self.cfg)[0])
        self.assertFalse(col.set_collection("Python/lists.py", "completed", None, self.cfg)[0])  # a file
        self.assertFalse(col.set_collection("Python", "finished", None, self.cfg)[0])          # bad status

    def test_no_new_permission_and_unknown_resets(self):
        before = load_config(self.cfg).config
        col.set_collection(YEAR_A, "completed", None, self.cfg)
        after = load_config(self.cfg).config
        self.assertEqual((after.approved_folder, after.grant_id, after.transfer_approval),
                         (before.approved_folder, before.grant_id, before.transfer_approval))
        col.set_collection(YEAR_A, "unknown", None, self.cfg)
        self.assertNotIn(YEAR_A, load_config(self.cfg).config.collections)

    def test_new_unclassified_folder_is_asked_once_per_folder(self):
        seen = self.base / "seen.json"
        rows = col.overview(self.cfg)["collections"]
        self.assertEqual(sorted(col.new_unclassified(rows, seen)), sorted(["C#", "Python", YEAR_A]))
        self.assertEqual(col.new_unclassified(rows, seen), [])               # not asked again
        (self.root / "Physics").mkdir()
        self.write("Physics/a.txt", "x")
        self.assertEqual(col.new_unclassified(col.overview(self.cfg)["collections"], seen), ["Physics"])

    def test_classification_survives_revoke_and_regrant_of_the_same_folder_only(self):
        col.set_collection(YEAR_A, "completed", None, self.cfg)
        folder_access.revoke_folder(self.cfg)
        folder_access.grant_folder(str(self.root), self.cfg, home=self.base / "nohome")
        self.assertIn(YEAR_A, load_config(self.cfg).config.active_collections())
        other = self.base / "other"
        other.mkdir()
        folder_access.grant_folder(str(other), self.cfg, home=self.base / "nohome")
        self.assertEqual(load_config(self.cfg).config.active_collections(), {})

    def test_corrupted_classification_fails_closed(self):
        data = at_rest.read(self.cfg)             # settings are encrypted at rest
        data["collections"] = {"../x": {"study_status": "completed"}}
        self.cfg.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(load_config(self.cfg).config.approved_folder)


class TestQueues(Base):
    def test_before_start_everything_is_backfill_and_new_files_after_start_are_live(self):
        self.assertEqual(col.queue_for("2026-01-01T00:00:00+00:00", None), "backfill")
        col.set_collection(YEAR_A, "completed", YEAR_A, self.cfg)
        self.assertTrue(col.start_live(self.cfg)[0])
        self.assertFalse(col.start_live(self.cfg)[0])                     # set once, never moved
        time.sleep(1.1)
        self.write("Python/new_lesson.md", "dicts")                      # new after the start
        self.write(f"{YEAR_A}/C#/stack.md", "Stack: LIFO, edited now")    # old file changed now
        self.scan()
        self.allow_transfer()
        report = self.client().run()
        queues = [(e["file"]["rel_path"], e["context"]["queue"], e["context"]["study_status"]) for e in self.rx.got]
        live = [q for q in queues if q[1] == "live"]
        self.assertEqual(sorted(r for r, _q, _s in live), sorted(["Python/new_lesson.md", f"{YEAR_A}/C#/stack.md"]))
        self.assertTrue(all(q[1] == "live" for q in queues[:2]))          # live is sent FIRST
        self.assertIn((f"{YEAR_A}/C#/stack.md", "live", "completed"), queues)   # time ≠ study status
        self.assertEqual(report["live_pending"], 2)

    def test_daily_backfill_budget_and_resume_next_day(self):
        cfg = at_rest.read(self.cfg)
        cfg["backfill_daily_chars"] = 25
        self.cfg.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.scan()
        self.allow_transfer()
        r1 = self.client("2026-11-24").run()
        self.assertGreater(r1["backfill_deferred"], 0)
        first = len(self.rx.got)
        r2 = self.client("2026-11-24").run()                              # same day: nothing more
        self.assertEqual(len(self.rx.got), first)
        self.client("2026-11-25").run()                                   # next day continues
        self.assertGreater(len(self.rx.got), first)
        sent = [e["file"]["rel_path"] for e in self.rx.got]
        self.assertEqual(len(sent), len(set(sent)))                       # never twice

    def test_backfill_goes_folder_by_folder_and_progress_counts(self):
        self.scan()
        self.allow_transfer()
        p0 = col.progress(self.cfg, self.base / "outbox", self.base / "t.json", self.base / "state.json")
        self.assertEqual((p0["backfill_files"], p0["backfill_done"]), (4, 0))
        self.client().run()
        order = [e["context"]["collection"] for e in self.rx.got]
        self.assertEqual(order, sorted(order))
        p1 = col.progress(self.cfg, self.base / "outbox", self.base / "t.json", self.base / "state.json")
        self.assertEqual((p1["backfill_done"], p1["backfill_percent"]), (4, 100.0))

    def test_envelopes_carry_the_explicit_classification_and_file_date(self):
        col.set_collection(YEAR_A, "completed", YEAR_A, self.cfg)
        self.scan()
        self.allow_transfer()
        self.client().run()
        env = next(e for e in self.rx.got if e["file"]["rel_path"] == f"{YEAR_A}/notes.txt")
        self.assertEqual((env["context"]["study_status"], env["context"]["period"], env["context"]["collection"]),
                         ("completed", YEAR_A, YEAR_A))
        self.assertTrue(env["context"]["modified_at"].startswith(time.strftime("%Y", time.gmtime(self.old))))


if __name__ == "__main__":
    unittest.main()
