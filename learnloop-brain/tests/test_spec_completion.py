"""Spec items completed on 2026-11-24 without new decisions (synthetic data only):
§35 large files in parts, two-hour cycle, early trigger, not interrupting study;
§13 Skill + related/partOf; §19 reinforcement by mistake type; §23 evolving project;
§41 project progress; §42 evidence-based strengths."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from brain.api.routes import handle
from brain.app import LearnLoopApp
from brain.ingestion.steps import Deps, merge_analyses, run_without_graph, split_windows
from brain.model_gateway import FakeModel
from brain.domain.repository import InMemoryRepository
from brain.scheduler import CycleScheduler
from brain.settings import Settings
from helpers import SOURCE, analysis, envelope

T0 = "2026-11-24T08:00:00+00:00"


def app_with(script, now):
    a = LearnLoopApp(FakeModel(script), clock=lambda: now[0], engine="plain")
    a.set_transfer_permission(SOURCE, True)
    return a


# --------------------------------------------------------------------------- §35 large files
class TestLargeFiles(unittest.TestCase):
    def test_split_keeps_order_and_limits(self):
        chunks = [{"chunk_id": f"c{i}", "text": "x" * 40} for i in range(5)]
        windows = split_windows(chunks, 100)
        self.assertEqual([[c["chunk_id"] for c in w] for w in windows], [["c0", "c1"], ["c2", "c3"], ["c4"]])

    def test_each_part_knows_the_earlier_parts_and_results_are_merged(self):
        def script(chunks, known):
            first = chunks[0]["chunk_id"]
            name = {"c0": "Lists", "c1": "Loops"}.get(first, "Dicts")
            return {"subject": {"name": "Python", "matched_subject_id": None, "confidence": 0.9},
                    "topics": [{"name": name, "confidence": 0.9, "chunk_ids": [first], "prerequisites": [],
                                "skills": [f"{name} basics"]},
                               {"name": "Lists", "confidence": 0.7, "chunk_ids": [first]}],
                    "evidence": [{"topic": name, "type": "exposure", "authorship": "unknown", "chunk_id": first}]}
        model = FakeModel(script)
        settings = Settings()
        settings.policy.analysis_window_chars = 30
        repo = InMemoryRepository(clock=lambda: T0)
        repo.set_transfer_permission(SOURCE, True)
        env = envelope(["a" * 25, "b" * 25, "c" * 25])
        state = run_without_graph(env, Deps(repo, model, settings=settings),
                                  decide=lambda reqs: [{"request_id": r["id"], "action": "approve"} for r in reqs])
        self.assertEqual(state["status"], "applied")
        self.assertEqual(len(model.calls), 3)
        self.assertIsNone(model.calls[0]["file_context"])
        self.assertEqual(model.calls[2]["file_context"]["subject"], "Python")
        self.assertIn("Loops", model.calls[2]["file_context"]["topics"])
        self.assertEqual(sorted(t.name for t in repo.topics.values()), ["Dicts", "Lists", "Loops"])
        lists = next(t for t in repo.topics.values() if t.name == "Lists")
        self.assertEqual(len([e for e in repo.evidence.values() if e.topic_id == lists.id]), 1)

    def test_one_failing_part_fails_the_whole_file_and_writes_nothing(self):
        calls = {"n": 0}

        def script(chunks, known):
            calls["n"] += 1
            if chunks[0]["chunk_id"] == "c1":
                return {"bad": True}
            return analysis()
        settings = Settings()
        settings.policy.analysis_window_chars = 30
        repo = InMemoryRepository(clock=lambda: T0)
        repo.set_transfer_permission(SOURCE, True)
        state = run_without_graph(envelope(["a" * 25, "b" * 25]), Deps(repo, FakeModel(script), settings=settings),
                                  decide=lambda r: [])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(repo.topics, {})

    def test_a_file_over_the_provider_budget_now_fits(self):
        from brain.providers import build_gateway, from_dict
        from brain.providers.simulator import handler_for
        gw = build_gateway(from_dict({"provider": "gemini", "transport": "mock"}),
                           mock_handler=handler_for("gemini", analysis(topics=(("Lists", 0.9),))))
        repo = InMemoryRepository(clock=lambda: T0)
        repo.set_transfer_permission(SOURCE, True)
        env = envelope(["א" * 60_000, "ב" * 60_000, "ג" * 60_000])        # 180k chars > 120k budget
        state = run_without_graph(env, Deps(repo, gw, settings=Settings()),
                                  decide=lambda reqs: [{"request_id": r["id"], "action": "approve"} for r in reqs])
        self.assertEqual(state["status"], "applied")
        self.assertEqual(len(gw.transport.requests), 3)          # 60k + 60k > 90k → three parts
        self.assertNotIn("input_too_large", {s["outcome"] for s in gw.stats})

    def test_merge_prefers_the_most_frequent_subject(self):
        a = analysis(subject="Python")
        b = analysis(subject="python")
        c = analysis(subject="Math")
        self.assertEqual(merge_analyses([c, a, b])["subject"]["name"].lower(), "python")


# --------------------------------------------------------------------------- §35 schedule
class TestSchedule(unittest.TestCase):
    def setUp(self):
        self.now = [T0]
        self.app = app_with(analysis(topics=(("Lists", 0.9),)), self.now)
        self.sched = CycleScheduler(self.app)

    def at(self, hours):
        t = datetime.fromisoformat(T0) + timedelta(hours=hours)
        self.now[0] = t.isoformat()
        return t

    def test_nothing_waiting_means_no_ai_call(self):
        self.assertEqual(self.sched.tick(self.at(0))["decision"], "idle")

    def test_two_hour_cycle(self):
        self.app.receive(envelope(["numbers.sort()"], rel_path="a.py"))
        self.assertEqual(self.sched.tick(self.at(0))["decision"], "scheduled")       # first cycle
        self.app.receive(envelope(["more"], rel_path="b.py"))
        self.assertEqual(self.sched.tick(self.at(1))["decision"], "idle")            # a small change waits
        self.assertEqual(self.sched.tick(self.at(2))["decision"], "scheduled")

    def test_accumulation_triggers_early(self):
        self.app.run_cycle()                                                         # last cycle now
        self.app.settings.schedule.accumulation_files = 3
        for i in range(3):
            self.app.receive(envelope([f"x{i}"], rel_path=f"f{i}.py"))
        self.assertEqual(self.sched.tick(self.at(0.5))["decision"], "accumulation")

    def test_studying_now_postpones_and_is_recorded_once(self):
        self.app.receive(envelope(["numbers.sort()"], rel_path="a.py"))
        self.sched.tick(self.at(0))
        for p in self.app.pending_approvals():
            self.app.decide(p["id"], "approve")
        topic = next(iter(self.app.repo.topics))
        self.at(2)
        act = self.app.start_activity(topic, "quiz", "learn")
        self.app.receive(envelope(["new material"], rel_path="b.py"))
        self.assertEqual(self.sched.tick(self.at(2.1))["decision"], "deferred_studying")
        self.assertEqual(self.sched.tick(self.at(2.15))["decision"], "deferred_studying")
        self.assertEqual(len([r for r in self.app.repo.history if r.action == "deferred"]), 1)
        self.assertTrue(self.sched.status(self.at(2.15))["deferred_because_studying"])
        self.app.answer(act["id"], "q1", 1)
        self.app.finish_activity(act["id"])
        self.assertEqual(self.sched.tick(self.at(2.2))["decision"], "scheduled")     # after she finished

    def test_explicit_request_always_runs(self):
        self.app.receive(envelope(["numbers.sort()"], rel_path="a.py"))
        self.assertEqual(self.app.run_cycle()["processed"], 1)
        rec = [r for r in self.app.repo.history if r.entity == "cycle"][-1]
        self.assertEqual((rec.entity_id, rec.mode), ("user_request", "user"))

    def test_status_route(self):
        status, body = handle(self.app, "GET", "/api/schedule")
        self.assertEqual((status, body["interval_hours"]), (200, 2))


# --------------------------------------------------------------------------- learning features
class Learning(unittest.TestCase):
    def setUp(self):
        self.now = [T0]
        script = {"subject": {"name": "Python", "matched_subject_id": None, "confidence": 0.9},
                  "topics": [{"name": "Lists", "confidence": 0.9, "chunk_ids": ["c0"], "related": ["Loops"],
                              "skills": ["Reverse a list", "Sort a list"]},
                             {"name": "Loops", "confidence": 0.9, "chunk_ids": ["c0"]}],
                  "evidence": [{"topic": "Lists", "type": "exposure", "authorship": "unknown", "chunk_id": "c0",
                                "skill": "Sort a list"}]}
        self.app = app_with(script, self.now)
        self.app.receive(envelope(["numbers.sort()"]))
        self.app.run_cycle()
        for p in self.app.pending_approvals():
            self.app.decide(p["id"], "approve")
        self.sid = next(iter(self.app.repo.subjects))
        self.lists = next(t.id for t in self.app.repo.topics.values() if t.name == "Lists")

    def later(self, days):
        self.now[0] = (datetime.fromisoformat(T0) + timedelta(days=days)).isoformat()


class TestSkillsAndRelations(Learning):
    def test_skills_are_part_of_their_topic_and_related_is_stored_once(self):
        d = self.app.dashboard(self.sid)
        lists = next(t for t in d["topics"] if t["name"] == "Lists")
        self.assertEqual(lists["skills"], ["Reverse a list", "Sort a list"])
        kinds = sorted((m["kind"], m["from"]) for m in d["skill_map"])
        self.assertIn(("partOf", "Sort a list"), kinds)
        self.assertEqual(sum(1 for m in d["skill_map"] if m["kind"] == "related"), 1)
        ev = next(iter(self.app.repo.evidence.values()))
        self.assertEqual(self.app.repo.skills[ev.skill_id].name, "Sort a list")

    def test_skills_survive_a_restart(self):
        from brain.storage.crypto import AesGcmCipher
        from brain.storage.db import SqliteDatabase
        from brain.storage.store import Store
        with tempfile.TemporaryDirectory() as d:
            db = SqliteDatabase(str(Path(d) / "s.db"))
            self.app.store = Store(db, AesGcmCipher(b"k" * 32))
            self.app.commit()
            again = LearnLoopApp(FakeModel({}), engine="plain", store=Store(db, AesGcmCipher(b"k" * 32)))
            self.assertEqual(sorted(k.name for k in again.repo.skills.values()), ["Reverse a list", "Sort a list"])
            self.assertEqual({e.skill_id for e in again.repo.evidence.values()},
                             {e.skill_id for e in self.app.repo.evidence.values()})
            db.close()


class TestMistakeTypes(Learning):
    def answer_open(self, mistake):
        act = self.app.start_activity(self.lists, "quiz", "learn")
        self.app.activities.model.grade = lambda item, answer, lang="he": {
            "score": 0.2, "feedback": "כדאי לחזור להגדרה", "mistake_type": mistake}
        for item in act["items"]:
            self.app.answer(act["id"], item["id"], 0 if item["kind"] == "multiple_choice" else "not quite")
        self.app.finish_activity(act["id"])

    def test_reinforcement_follows_the_type_of_mistake(self):
        for _ in range(3):
            self.answer_open("misconception")
        topic = self.app.repo.topics[self.lists]
        self.assertEqual(topic.state, "reinforce")
        rec = self.app.activities.recommend(self.sid)
        self.assertEqual((rec["activity_type"], rec["reason_key"]), ("summary", "rec.mistake_misconception"))
        act = self.app.start_activity(self.lists, rec["activity_type"], rec["purpose"])
        self.assertEqual(act["type"], "summary")


class TestProjectsAndStrengths(Learning):
    def test_project_progress_and_the_project_evolves(self):
        specs, original = [], self.app.activities.model.activity
        self.app.activities.model.activity = lambda spec: (specs.append(spec), original(spec))[1]
        p1 = self.app.start_activity(self.lists, "project", "understand_code")
        self.app.answer(p1["id"], p1["items"][0]["id"], "it sorts the list")
        self.app.finish_activity(p1["id"])
        self.app.start_activity(self.lists, "project", "understand_code")
        self.assertNotIn("previous_project", specs[0])
        self.assertEqual(specs[1]["previous_project"]["title"], p1["title"])     # §23: continues the project
        prog = self.app.dashboard(self.sid)["project_progress"]
        self.assertEqual((prog["projects"], prog["finished"], prog["items_answered"]), (2, 1, 1))

    def test_no_strengths_without_evidence(self):
        self.assertEqual([s for s in self.app.strengths() if s["kind"] != "success"], [])

    def test_strength_from_her_own_strong_explanations(self):
        for i in range(3):
            act = self.app.start_activity(self.lists, "quiz", "learn")
            self.app.activities.model.grade = lambda item, answer, lang="he": {
                "score": 0.95, "feedback": "מצוין", "mistake_type": "none"}
            for item in act["items"]:
                self.app.answer(act["id"], item["id"], 1 if item["kind"] == "multiple_choice" else "a good explanation")
            self.app.finish_activity(act["id"])
            self.later(i + 1)
        kinds = {s["kind"] for s in self.app.strengths()}
        self.assertIn("ability", kinds)
        ability = next(s for s in self.app.strengths() if s["kind"] == "ability")
        self.assertGreaterEqual(ability["count"], 3)
        status, body = handle(self.app, "GET", "/api/strengths")
        self.assertEqual(status, 200)
        self.assertTrue(body["strengths"])

    def test_ai_generated_work_never_becomes_a_strength(self):
        from brain.domain.models import Evidence
        for i in range(5):
            self.app.repo.add_evidence(Evidence(f"evx{i}", self.lists, "apply", "ai", "c0", counts_as_user_ability=False,
                                                score=1.0, occurred_at=T0), "test", "autonomous")
        self.assertNotIn("apply", {s.get("evidence_type") for s in self.app.strengths()})


if __name__ == "__main__":
    unittest.main()
