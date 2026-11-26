"""Completed-material track (spec §21, APPROVED; decision 2026-11-24): broad quiz →
project/activity → knowledge picture → weekly refresher. Synthetic data only."""

import unittest

from brain.app import LearnLoopApp
from brain.model_gateway import FakeModel
from helpers import SOURCE, analysis, envelope

YEAR_A = "שנה א'"


def env_ctx(texts, rel, collection, status):
    e = envelope(texts, rel_path=rel)
    e["context"] = {"collection": collection, "study_status": status, "period": None, "queue": "backfill",
                    "modified_at": "2025-03-01T08:00:00+00:00"}
    return e


class Track(unittest.TestCase):
    def setUp(self):
        self.now = ["2026-11-24T10:00:00+00:00"]
        self.topics = (("Stack", 0.9), ("Queue", 0.9), ("Heap", 0.9))
        self.app = LearnLoopApp(FakeModel(lambda c, k: analysis(
            subject="C#", topics=self.topics, matched=next((s["id"] for s in k if s["name"] == "C#"), None))),
            clock=lambda: self.now[0], engine="plain")
        self.app.set_transfer_permission(SOURCE, True)

    def ingest(self, env):
        self.app.receive(env)
        self.app.run_cycle()
        for p in self.app.pending_approvals():
            self.app.decide(p["id"], "approve")
        return next(iter(self.app.repo.subjects))

    def do(self, activity, answers):
        for item in activity["items"]:
            if item["kind"] == "multiple_choice":
                self.app.answer(activity["id"], item["id"], answers)
            else:
                self.app.answer(activity["id"], item["id"], "key idea of it")
        return self.app.finish_activity(activity["id"])

    def test_the_approved_path_in_order(self):
        sid = self.ingest(env_ctx(["Stack Queue Heap"], f"{YEAR_A}/C#/ds.md", YEAR_A, "completed"))
        d = self.app.dashboard(sid)
        self.assertNotIn("understood", {t["state"] for t in d["topics"]})       # finishing ≠ understanding
        rec = self.app.next_step(sid)
        self.assertEqual((rec["action"], rec["step"], rec["activity_type"]), ("start_track", "placement", "quiz"))
        placement = self.app.start_track(sid)
        self.assertEqual(len(placement["items"]), 3)                           # broad: every topic
        self.do(placement, 1)
        self.assertEqual({t["assessment"] for t in self.app.dashboard(sid)["topics"]}, {"assessed"})
        self.assertEqual(self.app.next_step(sid)["step"], "track_project")
        project = self.app.start_track(sid)
        self.assertEqual(project["type"], "project")
        self.do(project, 1)
        self.assertEqual(self.app.dashboard(sid)["completed_track"]["step"], "waiting")
        self.assertIsNone(self.app.next_step(sid))                              # nothing pushed meanwhile
        self.now[0] = "2026-12-02T10:00:00+00:00"                               # a week later
        rec = self.app.next_step(sid)
        self.assertEqual((rec["step"], rec["choices"]), ("refresh", ["quiz", "game", "summary"]))
        refresh = self.app.start_track(sid)
        result = self.do(refresh, 1)
        self.assertIn(self.app.repo.evidence[[e for e in self.app.repo.evidence][-1]].type, ("retention",))
        self.assertIsNotNone(result)

    def test_one_broad_quiz_does_not_make_everything_understood(self):
        sid = self.ingest(env_ctx(["Stack Queue Heap"], f"{YEAR_A}/C#/ds.md", YEAR_A, "completed"))
        self.do(self.app.start_track(sid), 1)
        self.assertNotIn("understood", {t["state"] for t in self.app.dashboard(sid)["topics"]})

    def test_skipping_a_track_step_costs_nothing(self):
        sid = self.ingest(env_ctx(["Stack Queue Heap"], f"{YEAR_A}/C#/ds.md", YEAR_A, "completed"))
        before = [(t.state, t.score) for t in self.app.repo.topics.values()]
        act = self.app.start_track(sid)
        self.app.skip_activity(act["id"])
        self.assertEqual([(t.state, t.score) for t in self.app.repo.topics.values()], before)
        self.assertEqual(self.app.next_step(sid)["step"], "placement")           # may be offered again

    def test_current_material_follows_the_regular_path(self):
        sid = self.ingest(env_ctx(["Stack Queue Heap"], "C#/now.md", "C#", "active"))
        rec = self.app.next_step(sid)
        self.assertEqual(rec["action"], "start")
        self.assertIsNone(self.app.dashboard(sid)["completed_track"])

    def test_same_subject_two_periods_keeps_both_paths(self):
        sid = self.ingest(env_ctx(["Stack Queue Heap"], f"{YEAR_A}/C#/ds.md", YEAR_A, "completed"))
        self.topics = (("LINQ", 0.9),)
        self.ingest(env_ctx(["LINQ"], "C#/linq.md", "C#", "active"))
        rec = self.app.next_step(sid)
        self.assertEqual(rec["action"], "start")                               # current study first
        self.assertEqual(self.app.repo.topics[rec["topic_id"]].name, "LINQ")
        self.assertEqual(self.app.dashboard(sid)["completed_track"]["step"], "placement")   # still visible

    def test_unknown_material_is_not_put_on_the_track(self):
        sid = self.ingest(env_ctx(["Stack Queue Heap"], "misc/ds.md", "misc", "unknown"))
        self.assertIsNone(self.app.dashboard(sid)["completed_track"])


if __name__ == "__main__":
    unittest.main()
