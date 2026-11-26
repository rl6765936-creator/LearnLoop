import unittest

from brain.domain.models import Edge, Evidence
from brain.domain.repository import InMemoryRepository


class TestRepository(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryRepository(clock=lambda: "t")

    def test_transfer_permission_fails_closed(self):
        self.assertFalse(self.repo.may_receive_from("unknown-source"))

    def test_names_match_case_and_space_insensitive(self):
        s = self.repo.add_subject("Data  Structures", "req_1", "approved")
        self.assertIs(self.repo.find_subject("data structures"), s)

    def test_every_change_is_recorded_and_history_only_grows(self):
        s = self.repo.add_subject("C#", "req_1", "approved")
        t = self.repo.add_topic(s.id, "Stack", "seen", "autonomous")
        self.repo.add_evidence(Evidence("ev_1", t.id, "quiz", "user", "c0", True), "q", "autonomous")
        self.assertEqual([r.entity for r in self.repo.history], ["subject", "topic", "evidence"])
        self.assertEqual(self.repo.history[0].mode, "approved")
        self.assertEqual([r.seq for r in self.repo.history], [1, 2, 3])

    def test_topic_timeline_includes_its_evidence(self):
        s = self.repo.add_subject("C#", "req_1", "approved")
        t = self.repo.add_topic(s.id, "Stack", "seen", "autonomous")
        self.repo.add_evidence(Evidence("ev_1", t.id, "quiz", "user", "c0", True), "q", "autonomous")
        self.assertEqual([r.entity for r in self.repo.timeline(t.id)], ["topic", "evidence"])

    def test_prerequisite_cycle_detection(self):
        self.repo.add_edge(Edge("a", "b", "prerequisite"), "r", "autonomous")
        self.repo.add_edge(Edge("b", "c", "prerequisite"), "r", "autonomous")
        self.assertTrue(self.repo.would_create_cycle("c", "a"))
        self.assertFalse(self.repo.would_create_cycle("a", "c"))
        self.assertTrue(self.repo.would_create_cycle("a", "a"))

    def test_same_edge_is_added_once(self):
        self.assertTrue(self.repo.add_edge(Edge("a", "b", "related"), "r", "autonomous"))
        self.assertFalse(self.repo.add_edge(Edge("a", "b", "related"), "r", "autonomous"))


if __name__ == "__main__":
    unittest.main()
