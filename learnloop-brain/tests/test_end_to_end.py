"""The full learning cycle the user defined (11 steps), synthetic data + FakeModel.
Runs with the plain engine everywhere and with LangGraph where it is installed."""

import importlib.util
import json
import unittest

from brain.app import LearnLoopApp
from brain.model_gateway import FakeModel
from helpers import SOURCE, analysis, envelope

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
PY_FILE = "numbers = [3, 1, 2]\nnumbers.sort()\nfor n in numbers:\n    print(n)"


class FullCycle:
    engine = "plain"

    def setUp(self):
        self.now = ["2026-11-24T10:00:00+00:00"]
        self.model = FakeModel(analysis(subject="Python", topics=(("Lists", 0.9), ("Loops", 0.85)),
                                        prerequisites={"Lists": ["Loops"]}))
        self.app = LearnLoopApp(self.model, clock=lambda: self.now[0], engine=self.engine)
        self.app.set_transfer_permission(SOURCE, True)

    def test_the_eleven_steps(self):
        app = self.app
        # 1. study material arrives (as raw JSON bytes, like over the network)
        ack = app.receive(json.dumps(envelope([PY_FILE], rel_path="python/lists.py")).encode())
        self.assertTrue(ack["accepted"])
        # 2-3. analysis identifies subject and topics -> 5. new subject needs approval
        cycle = app.run_cycle()
        self.assertEqual(cycle["waiting"], 1)
        pending = app.pending_approvals()
        self.assertEqual([p["kind"] for p in pending], ["new_subject"])
        self.assertEqual(app.subject_spaces(), [])                    # nothing before approval
        # 4-6. user approves -> graph, evidence and history are updated
        result = app.decide(pending[0]["id"], "approve")
        self.assertEqual(result["status"], "applied")
        space = app.subject_spaces()[0]
        self.assertEqual(space["name"], "Python")
        dash = app.dashboard(space["id"])
        self.assertEqual(sorted(t["name"] for t in dash["topics"]), ["Lists", "Loops"])
        self.assertEqual(dash["skill_map"], [{"from": "Loops", "to": "Lists", "kind": "prerequisite"}])
        # 7. knowledge picture: exposure only -> learning, no understanding claimed
        self.assertEqual({t["state"] for t in dash["topics"]}, {"learning"})
        self.assertIsNone(dash["estimated_mastery"])
        # 8. a fitting activity: the foundation (Loops) first
        rec = app.next_step(space["id"])
        loops = next(t for t in dash["topics"] if t["name"] == "Loops")
        self.assertEqual((rec["topic_id"], rec["activity_type"]), (loops["id"], "quiz"))
        # 9. the user does it and gets feedback
        activity = app.start_activity(rec["topic_id"], rec["activity_type"], rec["purpose"])
        self.assertNotIn("answer_index", repr(activity))
        fb1 = app.answer(activity["id"], "q1", 1)
        fb2 = app.answer(activity["id"], "q2", "Loops key idea is repetition")
        self.assertEqual(fb1["feedback"], "נכון.")
        self.assertTrue(fb2["feedback"])
        # 10. results return to the knowledge graph (with history)
        done = app.finish_activity(activity["id"])
        self.assertEqual(done["mastery_changes"][0]["after"]["state"], "understood")
        history = app.topic_history(loops["id"])
        self.assertIn("mastery_updated", [h["action"] for h in history])
        # 11. the next step moves on to Lists, whose foundation is now understood
        nxt = done["next"]
        lists = next(t for t in dash["topics"] if t["name"] == "Lists")
        self.assertEqual((nxt["topic_id"], nxt["activity_type"]), (lists["id"], "quiz"))
        self.assertFalse(nxt["foundation"])
        # transparency summaries were produced along the way
        self.assertTrue(any("ראיות" in t for t in app.cycle_log))

    def test_revoked_transfer_permission_blocks_new_material(self):
        self.app.set_transfer_permission(SOURCE, False)
        ack = self.app.receive(envelope([PY_FILE]))
        self.assertFalse(ack["accepted"])
        self.assertEqual(self.app.run_cycle()["processed"], 0)

    def test_ignoring_the_recommendation_changes_nothing(self):
        self.app.receive(envelope([PY_FILE]))
        self.app.run_cycle()
        self.app.decide(self.app.pending_approvals()[0]["id"], "approve")
        before = [(t.state, t.score) for t in self.app.repo.topics.values()]
        self.app.next_step()                                          # shown, ignored
        self.now[0] = "2026-12-24T10:00:00+00:00"                     # a month later
        self.assertEqual([(t.state, t.score) for t in self.app.repo.topics.values()], before)
        self.assertIsNotNone(self.app.next_step())                    # may be suggested again


class TestFullCyclePlain(FullCycle, unittest.TestCase):
    engine = "plain"


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph is not installed in this environment")
class TestFullCycleLangGraph(FullCycle, unittest.TestCase):
    engine = "langgraph"


if __name__ == "__main__":
    unittest.main()


class TestDemoAndGrouping(unittest.TestCase):
    def test_same_new_subject_from_many_files_is_asked_once(self):
        from brain.demo import build_demo_app
        app = build_demo_app(clock=lambda: "2026-11-24T10:00:00+00:00")
        pending = app.pending_approvals()
        python = [p for p in pending if p["kind"] == "new_subject" and "Python" in p["summary"]]
        self.assertEqual(len(python), 1)
        self.assertEqual(python[0]["count"], 4)
        app.decide(python[0]["id"], "approve")
        self.assertEqual([s["name"] for s in app.subject_spaces()], ["Python"])
        self.assertEqual(len(app.repo.subjects), 1)
        dash = app.dashboard(app.subject_spaces()[0]["id"])
        self.assertEqual(sorted(t["name"] for t in dash["topics"]), ["Dictionaries", "Lists", "Loops"])
        # the unsure topic (Decorators) is now a separate, real question for the user
        kinds = [p["kind"] for p in app.pending_approvals()]
        self.assertIn("low_confidence", kinds)
        twin_records = [r for r in app.repo.history if r.entity == "approval"]
        self.assertTrue(all(r.mode == "user" for r in twin_records))
