"""Supervisor: routing, one thread per batch, Approval Center wired to pause/resume,
retry after failure, cycle transparency. Needs `langgraph` (project venv)."""

import importlib.util
import unittest

from brain.model_gateway import FailingModel, FakeModel
from helpers import analysis, deps, envelope

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def content(env):
    return {"type": "content", "envelope": env}


class SupervisorContract:
    """The same behaviour is required from both engines."""

    def make(self, d):
        raise NotImplementedError

    def setUp(self):
        self.d = deps()
        self.sup = self.make(self.d)

    def known_python(self):
        self.d.repo.add_subject("Python", "req_x", "earlier")

    def test_routine_cycle_is_applied_with_a_transparency_summary(self):
        self.known_python()
        summary = self.sup.run_cycle([content(envelope(["x"]))])
        self.assertEqual(len(summary.applied), 1)
        self.assertIn("ראיות", summary.text())
        self.assertEqual(self.sup.center.pending(), [])

    def test_new_subject_waits_in_the_approval_center_and_writes_nothing(self):
        summary = self.sup.run_cycle([content(envelope(["x"]))])
        self.assertEqual(len(summary.waiting), 1)
        self.assertIn("ממתינים להחלטה שלך", summary.text())
        self.assertEqual([r["kind"] for r in self.sup.center.pending()], ["new_subject"])
        self.assertEqual(self.d.repo.subjects, {})

    def test_resending_while_waiting_does_not_run_again(self):
        env = envelope(["x"])
        self.sup.run_cycle([content(env)])
        result = self.sup.handle(content(env))
        self.assertEqual(result["status"], "waiting_for_user")
        self.assertEqual(len(self.d.model.calls), 1)
        self.assertEqual(len(self.sup.center.pending()), 1)

    def test_user_decision_resumes_the_thread(self):
        self.sup.run_cycle([content(envelope(["x"]))])
        request = self.sup.center.pending()[0]
        result = self.sup.decide(request["id"], "approve")
        self.assertEqual(result["status"], "applied")
        self.assertEqual([s.name for s in self.d.repo.subjects.values()], ["Python"])
        modes = [(r.entity, r.mode) for r in self.d.repo.history]
        self.assertIn(("approval", "user"), modes)
        self.assertIn(("subject", "approved"), modes)

    def test_thread_resumes_only_after_all_its_requests_are_decided(self):
        self.d.model.script = analysis(topics=(("Lists", 0.9), ("Odd", 0.2)))
        self.sup.run_cycle([content(envelope(["x"]))])
        first, second = self.sup.center.pending()
        self.assertIsNone(self.sup.decide(first["id"], "approve"))
        self.assertEqual(self.d.repo.subjects, {})
        result = self.sup.decide(second["id"], "reject")
        self.assertEqual(result["status"], "applied")
        self.assertEqual([t.name for t in self.d.repo.topics.values()], ["Lists"])

    def test_correction_through_the_supervisor(self):
        self.sup.run_cycle([content(envelope(["x"]))])
        request = self.sup.center.pending()[0]
        self.sup.decide(request["id"], "correct", {"name": "Python 3"})
        self.assertEqual([s.name for s in self.d.repo.subjects.values()], ["Python 3"])

    def test_waiting_batch_does_not_block_other_batches(self):
        self.known_python()
        self.d.model.script = lambda chunks, known: (
            analysis(subject="SQL", topics=(("Select", 0.9),)) if chunks[0]["text"] == "sql"
            else analysis())
        summary = self.sup.run_cycle([content(envelope(["sql"], rel_path="q.sql")),
                                      content(envelope(["x"], rel_path="a.py"))])
        self.assertEqual((len(summary.waiting), len(summary.applied)), (1, 1))

    def test_failed_batch_is_retried_in_the_next_cycle_without_old_state(self):
        self.known_python()
        self.d.model = FailingModel(failures=3, then=FakeModel(analysis()))
        self.sup = self.make(self.d)
        env = envelope(["x"])
        first = self.sup.run_cycle([content(env)])
        self.assertEqual(first.failed, ["python/lists.py"])
        self.assertIn("ינוסו שוב", first.text())
        second = self.sup.run_cycle([content(env)])
        self.assertEqual(len(second.applied), 1)
        self.assertEqual(len(self.d.repo.evidence), 1)

    def test_unsupported_event_types_do_nothing(self):
        summary = self.sup.run_cycle([{"type": "activity_result", "data": "quiz 100"}])
        self.assertEqual(summary.unsupported, 1)
        self.assertEqual(self.d.model.calls, [])

    def test_action_log_holds_no_content(self):
        self.sup.run_cycle([content(envelope(["SECRET STUDY TEXT"], rel_path="private/x.py"))])
        blob = repr(self.sup.actions)
        self.assertNotIn("SECRET STUDY TEXT", blob)
        self.assertNotIn("private/x.py", blob)


class TestSupervisorPlainEngine(SupervisorContract, unittest.TestCase):
    def make(self, d):
        from brain.ingestion.engines import PlainEngine
        from brain.supervisor import Supervisor
        return Supervisor(d, engine=PlainEngine(d))


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph is not installed in this environment")
class TestSupervisorLangGraph(SupervisorContract, unittest.TestCase):
    def make(self, d):
        from brain.supervisor import Supervisor
        return Supervisor(d)


if __name__ == "__main__":
    unittest.main()
