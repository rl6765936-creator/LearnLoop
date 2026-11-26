"""Study Status / Assessment Status / Mastery – three separate dimensions (decision 2026-11-24).
Synthetic data only."""

import tempfile
import unittest
from pathlib import Path

from brain.api.routes import handle
from brain.app import LearnLoopApp
from brain.model_gateway import FakeModel
from helpers import SOURCE, analysis, envelope

YEAR_A = "שנה א'"
OLD = "2025-03-01T08:00:00+00:00"


def env_with(texts, rel, collection, status, period=None, modified_at=OLD):
    e = envelope(texts, rel_path=rel)
    e["context"] = {"collection": collection, "study_status": status, "period": period,
                    "queue": "backfill", "modified_at": modified_at}
    return e


class Base(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel(lambda chunks, known: analysis(
            subject="C#", topics=(("Stack", 0.9),),
            matched=next((s["id"] for s in known if s["name"] == "C#"), None)))
        self.app = LearnLoopApp(self.model, clock=lambda: "2026-11-24T10:00:00+00:00", engine="plain")
        self.app.set_transfer_permission(SOURCE, True)

    def ingest(self, env):
        self.app.receive(env)
        self.app.run_cycle()
        for p in self.app.pending_approvals():
            self.app.decide(p["id"], "approve")

    def subject_id(self):
        return next(iter(self.app.repo.subjects))


class TestExplicitClassification(Base):
    def test_collector_setting_needs_no_question_and_keeps_file_date(self):
        self.ingest(env_with(["Stack: LIFO"], f"{YEAR_A}/C#/stack.md", YEAR_A, "completed", YEAR_A))
        self.assertEqual(self.app.pending_classifications(), [])
        periods = self.app.dashboard(self.subject_id())["periods"]
        self.assertEqual([(p["collection"], p["study_status"], p["decided_by"]) for p in periods],
                         [(YEAR_A, "completed", "collector_setting")])
        ev = next(iter(self.app.repo.evidence.values()))
        self.assertEqual(ev.occurred_at, OLD)                           # file date, not processing date
        self.assertIsNotNone(ev.context_id)

    def test_finished_course_is_never_marked_understood(self):
        self.ingest(env_with(["Stack: LIFO"], f"{YEAR_A}/C#/stack.md", YEAR_A, "completed"))
        topic = self.app.dashboard(self.subject_id())["topics"][0]
        self.assertNotEqual(topic["state"], "understood")
        self.assertEqual((topic["study"]["summary"], topic["assessment"]), ("completed", "not_assessed"))

    def test_no_evidence_is_not_presented_as_not_studied(self):
        d = None
        self.ingest(env_with(["Stack: LIFO"], f"{YEAR_A}/C#/stack.md", YEAR_A, "completed"))
        d = self.app.dashboard(self.subject_id())
        self.assertNotIn("not_learned", d)
        self.assertIn("no_evidence_yet", d)


class TestGroupedQuestions(Base):
    def test_unknown_material_gets_one_grouped_question_and_is_not_blocked(self):
        self.ingest(env_with(["Stack: LIFO"], "C#/a.md", "C#", "unknown"))
        self.ingest(env_with(["Stack push pop"], "C#/b.md", "C#", "unknown"))
        groups = self.app.pending_classifications()
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["subject"], len(groups[0]["items"]), groups[0]["items"][0]["files"]),
                         ("C#", 1, 2))
        self.assertEqual(len(self.app.repo.evidence), 2)                 # material processed meanwhile

    def test_answer_is_kept_and_the_question_is_not_asked_again(self):
        self.ingest(env_with(["Stack: LIFO"], "C#/a.md", "C#", "unknown"))
        qid = self.app.pending_classifications()[0]["items"][0]["id"]
        self.app.classify(qid, "active", "2026")
        self.assertEqual(self.app.pending_classifications(), [])
        self.ingest(env_with(["Stack again"], "C#/c.md", "C#", "unknown"))
        self.assertEqual(self.app.pending_classifications(), [])
        ctx = self.app.dashboard(self.subject_id())["periods"][0]
        self.assertEqual((ctx["study_status"], ctx["period"], ctx["decided_by"]), ("active", "2026", "user_decision"))
        rec = [r for r in self.app.repo.history if r.entity == "study_context" and r.action == "updated"][-1]
        self.assertEqual((rec.mode, rec.approval_id), ("user", qid))

    def test_invalid_answers_are_refused(self):
        self.ingest(env_with(["Stack: LIFO"], "C#/a.md", "C#", "unknown"))
        qid = self.app.pending_classifications()[0]["items"][0]["id"]
        with self.assertRaises(ValueError):
            self.app.classify(qid, "unknown")
        with self.assertRaises(ValueError):
            self.app.classify("cls_nope", "active")
        self.app.classify(qid, "completed")
        with self.assertRaises(ValueError):
            self.app.classify(qid, "active")                             # answered once

    def test_a_later_folder_setting_settles_the_open_question(self):
        self.ingest(env_with(["Stack: LIFO"], "C#/a.md", "C#", "unknown"))
        self.ingest(env_with(["Stack more"], "C#/d.md", "C#", "active", "2026"))
        self.assertEqual(self.app.pending_classifications(), [])

    def test_old_envelopes_without_context_are_unknown(self):
        self.ingest(envelope(["Stack: LIFO"], rel_path="stack.md"))
        self.assertEqual(self.app.pending_classifications()[0]["items"][0]["collection"], "")


class TestStudyPeriods(Base):
    def test_same_subject_in_two_periods_has_no_single_global_status(self):
        self.ingest(env_with(["Stack: LIFO"], f"{YEAR_A}/C#/stack.md", YEAR_A, "completed", YEAR_A))
        self.ingest(env_with(["Stack in a project"], "C#/now.md", "C#", "active", "2026",
                             modified_at="2026-11-20T08:00:00+00:00"))
        d = self.app.dashboard(self.subject_id())
        self.assertEqual(sorted(p["study_status"] for p in d["periods"]), ["active", "completed"])
        topic = d["topics"][0]
        self.assertEqual(topic["study"]["summary"], "mixed")
        self.assertEqual(sorted(c["study_status"] for c in topic["study"]["contexts"]), ["active", "completed"])
        contexts = {e.context_id for e in self.app.repo.evidence.values()}
        self.assertEqual(len(contexts), 2)                               # every evidence keeps its origin


class TestAssessment(Base):
    def test_assessed_only_after_her_own_answers(self):
        self.ingest(env_with(["Stack: LIFO"], f"{YEAR_A}/C#/stack.md", YEAR_A, "completed"))
        topic_id = next(iter(self.app.repo.topics))
        self.assertEqual(self.app.dashboard(self.subject_id())["topics"][0]["assessment"], "not_assessed")
        act = self.app.start_activity(topic_id, "quiz", "learn")
        self.app.answer(act["id"], "q1", 1)
        self.app.finish_activity(act["id"])
        self.assertEqual(self.app.dashboard(self.subject_id())["topics"][0]["assessment"], "assessed")


class TestPersistenceAndApi(Base):
    def test_contexts_questions_and_evidence_origin_survive_a_restart(self):
        from brain.storage.crypto import AesGcmCipher
        from brain.storage.db import SqliteDatabase
        from brain.storage.store import Store
        with tempfile.TemporaryDirectory() as d:
            db = SqliteDatabase(str(Path(d) / "t.db"))
            key = b"k" * 32
            self.app.store = Store(db, AesGcmCipher(key))
            self.ingest(env_with(["Stack: LIFO"], f"{YEAR_A}/C#/stack.md", YEAR_A, "completed", YEAR_A))
            self.ingest(env_with(["Queue"], "Other/q.md", "Other", "unknown"))
            self.app.commit()
            again = LearnLoopApp(self.model, engine="plain", store=Store(db, AesGcmCipher(key)))
            self.assertEqual(len(again.repo.study_contexts), len(self.app.repo.study_contexts))
            self.assertEqual(again.pending_classifications(), self.app.pending_classifications())
            self.assertEqual({e.context_id for e in again.repo.evidence.values()},
                             {e.context_id for e in self.app.repo.evidence.values()})
            raw = (Path(d) / "t.db").read_bytes()
            self.assertNotIn(YEAR_A.encode("utf-8"), raw)                 # folder names are encrypted
            db.close()

    def test_routes(self):
        self.ingest(env_with(["Stack: LIFO"], "C#/a.md", "C#", "unknown"))
        status, body = handle(self.app, "GET", "/api/classifications")
        qid = body["classifications"][0]["items"][0]["id"]
        status, body = handle(self.app, "POST", f"/api/classifications/{qid}", {"study_status": "completed"})
        self.assertEqual(status, 200)
        status, body = handle(self.app, "POST", f"/api/classifications/{qid}", {"study_status": "active"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
