"""Git commits and ActivityWatch summaries arriving from the local Collector (Q11, Q12)."""

import json
import tempfile
import unittest
from pathlib import Path

from brain.api.routes import handle
from brain.app import LearnLoopApp
from brain.model_gateway import FakeModel
from helpers import close_databases_after, analysis

NOW = "2026-11-24T10:00:00+00:00"


def summary(day="2026-11-23", apps=None):
    return {"schema_version": 1, "type": "activity_summary",
            "source": {"type": "activitywatch", "source_id": "activitywatch", "grant_id": "aw-1"},
            "day": day, "apps": apps if apps is not None else
            [{"app": "pycharm64.exe", "category": "לימודים > תכנות", "minutes": 40},
             {"app": "chrome.exe", "category": None, "minutes": 100}],
            "content_trust": "untrusted_user_data"}


class TestActivity(unittest.TestCase):
    def setUp(self):
        close_databases_after(self)
        self.app = LearnLoopApp(FakeModel(lambda c, k: analysis()), clock=lambda: NOW, engine="plain")

    def test_valid_summary_is_kept_and_shown(self):
        close_databases_after(self)
        self.assertTrue(self.app.receive_activity(json.dumps(summary()).encode())["accepted"])
        self.app.receive_activity(summary("2026-11-22", [{"app": "pycharm64.exe", "category": "לימודים > תכנות", "minutes": 20}]))
        body = handle(self.app, "GET", "/api/activity")[1]
        self.assertEqual(body["days"], ["2026-11-22", "2026-11-23"])
        self.assertEqual(body["apps"][1], {"app": "pycharm64.exe", "category": "לימודים > תכנות", "minutes": 60})

    def test_titles_are_refused_by_the_contract(self):
        close_databases_after(self)
        bad = summary()
        bad["apps"][0]["title"] = "Inbox – private mail"
        self.assertFalse(self.app.receive_activity(bad)["accepted"])
        self.assertEqual(self.app.activity_days, {})

    def test_persisted_encrypted(self):
        close_databases_after(self)
        import os
        from brain.storage.crypto import AesGcmCipher
        from brain.storage.db import SqliteDatabase
        from brain.storage.store import Store
        path, key = Path(tempfile.mkdtemp()) / "b.db", os.urandom(32)
        app = LearnLoopApp(FakeModel(lambda c, k: analysis()), clock=lambda: NOW, engine="plain",
                           store=Store(SqliteDatabase(str(path)), AesGcmCipher(key)))
        app.receive_activity(summary())
        app.commit()
        self.assertNotIn(b"pycharm64", path.read_bytes())
        again = LearnLoopApp(FakeModel(lambda c, k: analysis()), clock=lambda: NOW, engine="plain",
                             store=Store(SqliteDatabase(str(path)), AesGcmCipher(key)))
        self.assertIn("2026-11-23", again.activity_days)
        # backup -> restore on a "new computer" with ANOTHER key keeps the summaries
        from brain.storage.backup import create_backup, restore_backup
        bkey, bfile = AesGcmCipher(os.urandom(32)), path.with_suffix(".bak")
        create_backup(again.store, bkey, bfile)
        new = Store(SqliteDatabase(str(path.with_name("n.db"))), AesGcmCipher(os.urandom(32)))
        restore_backup(bfile, bkey, new)
        moved = LearnLoopApp(FakeModel(lambda c, k: analysis()), clock=lambda: NOW, engine="plain", store=new)
        self.assertEqual(moved.activity_days["2026-11-23"][0]["app"], "pycharm64.exe")


class TestGitEnvelope(unittest.TestCase):
    def test_git_source_type_is_accepted(self):
        close_databases_after(self)
        from brain import contracts
        from helpers import envelope
        env = envelope(["fix: sort the list"], rel_path="commits/abc123", source="git-1", grant="git-g")
        env["source"]["type"] = "git"
        contracts.validate("transfer_envelope", env)
        app = LearnLoopApp(FakeModel(lambda c, k: analysis()), clock=lambda: NOW, engine="plain")
        self.assertTrue(app.receive_from_collector(json.dumps(env).encode())["accepted"])


if __name__ == "__main__":
    unittest.main()


class TestActivityQuestions(unittest.TestCase):
    def test_ask_once_and_link_time_to_subject(self):
        close_databases_after(self)
        from helpers import envelope
        app = LearnLoopApp(FakeModel(lambda c, k: analysis(subject="Python")), clock=lambda: NOW, engine="plain")
        app.set_transfer_permission("s1", True)
        app.receive(envelope(["x = [1]"], source="s1"))
        app.run_cycle()
        for a in app.pending_approvals():
            app.decide(a["id"], "approve")
        sid = next(iter(app.repo.subjects))
        app.receive_activity(summary())
        qs = handle(app, "GET", "/api/system")[1]["questions"]
        self.assertEqual([q["app"] for q in qs], ["chrome.exe"])          # ≥60 min only
        status, _ = handle(app, "POST", "/api/activity/questions", {"app": "chrome.exe", "answer": sid})
        self.assertEqual(status, 200)
        self.assertEqual(app.activity_questions(), [])                    # not asked again
        self.assertEqual(app.dashboard(sid)["work_minutes_7d"], 100)
        before = {t.id: (t.state, t.score) for t in app.repo.topics.values()}
        self.assertEqual(before, {t.id: (t.state, t.score) for t in app.repo.topics.values()})  # no mastery change
        self.assertEqual(handle(app, "POST", "/api/activity/questions", {"app": "chrome.exe", "answer": "nope"})[0], 400)
