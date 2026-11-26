import unittest

from brain import contracts
from brain.approvals import ApprovalCenter
from brain.ingestion.steps import run_without_graph
from helpers import deps, envelope


class TestApprovalCenter(unittest.TestCase):
    def setUp(self):
        self.d = deps()
        self.center = ApprovalCenter(self.d.repo)
        self.state = run_without_graph(envelope(["x"]), self.d, decide=None)
        self.center.submit("thread-1", self.state["requests"])
        self.req = self.state["requests"][0]

    def test_pending_until_decided(self):
        self.assertEqual([r["id"] for r in self.center.pending()], [self.req["id"]])
        self.assertIsNone(self.center.ready("thread-1"))

    def test_decision_is_recorded_in_history_as_the_users(self):
        self.center.decide(self.req["id"], "approve")
        record = self.d.repo.history[-1]
        self.assertEqual((record.entity, record.mode, record.after["action"]),
                         ("approval", "user", "approve"))
        self.assertEqual(self.center.pending(), [])
        self.assertEqual(self.center.ready("thread-1"),
                         [{"request_id": self.req["id"], "action": "approve"}])

    def test_resubmitting_after_resume_does_not_duplicate(self):
        self.center.submit("thread-1", self.state["requests"])
        self.assertEqual(len(self.center.pending()), 1)

    def test_decision_cannot_be_given_twice_or_for_unknown_requests(self):
        self.center.decide(self.req["id"], "reject")
        with self.assertRaises(ValueError):
            self.center.decide(self.req["id"], "approve")
        with self.assertRaises(KeyError):
            self.center.decide("req_unknown", "approve")

    def test_invalid_decision_is_refused(self):
        with self.assertRaises(contracts.ContractError):
            self.center.decide(self.req["id"], "correct")       # correction missing

    def test_one_thread_waiting_does_not_block_another(self):
        other = run_without_graph(envelope(["y"], rel_path="b.py"), self.d, decide=None)
        self.center.submit("thread-2", other["requests"])
        self.center.decide(other["requests"][0]["id"], "approve")
        self.assertIsNotNone(self.center.ready("thread-2"))
        self.assertIsNone(self.center.ready("thread-1"))


if __name__ == "__main__":
    unittest.main()
