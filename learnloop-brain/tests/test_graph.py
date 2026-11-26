"""The LangGraph version of the ingestion workflow: pause, resume, idempotency.

Runs only where `langgraph` is installed (the project venv on Windows). Elsewhere the
tests are skipped – the same steps are covered without LangGraph in test_ingestion.py.
"""

import importlib.util
import unittest

from helpers import analysis, approve_all, deps, envelope, reject_all

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph is not installed in this environment")
class TestIngestionGraph(unittest.TestCase):
    def setUp(self):
        from langgraph.checkpoint.memory import InMemorySaver

        from brain.ingestion import graph as g
        self.g = g
        self.saver = InMemorySaver()
        self.d = deps()
        self.graph = g.build_graph(self.d, self.saver)

    def test_routine_batch_runs_to_the_end_without_pausing(self):
        self.d.repo.add_subject("Python", "req_x", "earlier")
        result = self.g.start(self.graph, envelope(["x"]), "t-routine")
        self.assertEqual(self.g.pending_requests(result), [])
        self.assertEqual(result["status"], "applied")
        self.assertIn("ראיות", result["transparency"])

    def test_new_subject_pauses_and_writes_nothing(self):
        result = self.g.start(self.graph, envelope(["x"]), "t-new")
        requests = self.g.pending_requests(result)
        self.assertEqual([r["kind"] for r in requests], ["new_subject"])
        self.assertEqual((self.d.repo.subjects, self.d.repo.content), ({}, {}))

    def test_resume_with_approval_applies_once(self):
        result = self.g.start(self.graph, envelope(["x"]), "t-approve")
        requests = self.g.pending_requests(result)
        result = self.g.resume(self.graph, "t-approve", approve_all(requests))
        self.assertEqual(result["status"], "applied")
        self.assertEqual([s.name for s in self.d.repo.subjects.values()], ["Python"])
        self.assertEqual(len(self.d.model.calls), 1)     # analysis was NOT repeated on resume

    def test_resume_with_rejection_changes_no_graph(self):
        result = self.g.start(self.graph, envelope(["x"]), "t-reject")
        result = self.g.resume(self.graph, "t-reject", reject_all(self.g.pending_requests(result)))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.d.repo.subjects, {})

    def test_incomplete_or_invalid_answer_keeps_waiting(self):
        self.d.model.script = analysis(topics=(("Lists", 0.9), ("Odd", 0.2)))
        result = self.g.start(self.graph, envelope(["x"]), "t-partial")
        requests = self.g.pending_requests(result)
        self.assertEqual(len(requests), 2)
        first = [{"request_id": requests[0]["id"], "action": "approve"},
                 {"request_id": requests[1]["id"], "action": "correct"}]   # invalid: no correction
        result = self.g.resume(self.graph, "t-partial", first)
        self.assertEqual(len(self.g.pending_requests(result)), 2)          # paused again
        self.assertEqual(self.d.repo.subjects, {})
        result = self.g.resume(self.graph, "t-partial", approve_all(requests))
        self.assertEqual(result["status"], "applied")

    def test_waiting_thread_does_not_block_another_thread(self):
        self.g.start(self.graph, envelope(["x"]), "t-waiting")
        self.d.repo.add_subject("Python", "req_x", "earlier")
        other = self.g.start(self.graph, envelope(["y"], rel_path="b.py"), "t-other")
        self.assertEqual(other["status"], "applied")

    def test_resume_after_a_restart_with_the_same_checkpoint_store(self):
        result = self.g.start(self.graph, envelope(["x"]), "t-restart")
        requests = self.g.pending_requests(result)
        restarted = self.g.build_graph(self.d, self.saver)      # new graph object, same saved state
        result = self.g.resume(restarted, "t-restart", approve_all(requests))
        self.assertEqual(result["status"], "applied")

    def test_duplicate_envelope_is_not_processed_again(self):
        self.d.repo.add_subject("Python", "req_x", "earlier")
        env = envelope(["x"])
        self.g.start(self.graph, env, "t-dup-1")
        result = self.g.start(self.graph, env, "t-dup-2")
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(len(self.d.repo.evidence), 1)

    def test_removal_path(self):
        self.d.repo.add_subject("Python", "req_x", "earlier")
        self.g.start(self.graph, envelope(["x"], rel_path="a.py"), "t-add")
        result = self.g.start(self.graph, envelope([], rel_path="a.py", change="removed"), "t-rm")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.d.repo.active_chunks(), [])

    def test_graph_and_plain_driver_reach_the_same_result(self):
        from brain.ingestion.steps import run_without_graph
        plain = deps()
        run_without_graph(envelope(["x"]), plain, approve_all)
        result = self.g.start(self.graph, envelope(["x"]), "t-parity")
        self.g.resume(self.graph, "t-parity", approve_all(self.g.pending_requests(result)))
        self.assertEqual([(r.entity, r.action, r.mode) for r in plain.repo.history],
                         [(r.entity, r.action, r.mode) for r in self.d.repo.history])


if __name__ == "__main__":
    unittest.main()
