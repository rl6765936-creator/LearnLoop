"""B5: activities, grading, feedback, evidence, recommendations, no-penalty rules."""

import unittest

from brain.activities import ActivityError, ActivityService
from brain.domain.mastery import ProvisionalMasteryModel
from brain.domain.models import Edge, Evidence
from brain.domain.repository import InMemoryRepository
from brain.model_gateway import FakeModel

T0 = "2026-11-24T10:00:00+00:00"


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.repo = InMemoryRepository(clock=self.clock)
        self.subject = self.repo.add_subject("Python", "req", "approved")
        self.lists = self.repo.add_topic(self.subject.id, "Lists", "seen", "autonomous")
        self.model = FakeModel({})
        self.svc = ActivityService(self.repo, self.model, ProvisionalMasteryModel())

    def quiz(self, topic=None, kind="quiz", purpose="learn"):
        a = self.svc.create((topic or self.lists).id, kind, purpose)
        self.svc.start(a.id)
        return a


class TestQuizFlow(Base):
    def test_view_never_reveals_answers(self):
        view = self.svc.start(self.svc.create(self.lists.id, "quiz").id)
        blob = repr(view)
        self.assertNotIn("answer_index", blob)
        self.assertNotIn("reference_answer", blob)

    def test_closed_question_graded_by_code_with_feedback(self):
        a = self.quiz()
        self.assertEqual(self.svc.answer(a.id, "q1", 1)["score"], 1.0)
        wrong = self.svc.answer(a.id, "q1", 0)
        self.assertEqual(wrong["score"], 0.0)
        self.assertIn("התשובה הנכונה", wrong["feedback"])
        self.assertNotIn({"grade_answer": "q1"}, self.model.calls)

    def test_open_answer_graded_by_model_and_validated(self):
        a = self.quiz()
        result = self.svc.answer(a.id, "q2", "the Lists key idea is order")
        self.assertGreaterEqual(result["score"], 0.8)
        self.model.grade = lambda item, ans: {"score": 5, "feedback": "x"}   # invalid proposal
        with self.assertRaises(Exception):
            self.svc.answer(a.id, "q2", "anything")

    def test_results_become_evidence_and_update_the_assessment(self):
        a = self.quiz()
        self.svc.answer(a.id, "q1", 1)
        self.svc.answer(a.id, "q2", "Lists key idea")
        result = self.svc.finish(a.id)
        evs = [e for e in self.repo.evidence.values() if e.activity_id == a.id]
        self.assertEqual(sorted(e.type for e in evs), ["explain", "quiz"])
        self.assertTrue(all(e.counts_as_user_ability and e.independence == "independent" for e in evs))
        self.assertEqual(self.lists.state, "understood")
        self.assertEqual(result["mastery_changes"][0]["after"]["state"], "understood")
        self.assertIsNotNone(result["next"])

    def test_hints_and_shown_solutions_are_recorded_as_less_independent(self):
        a = self.quiz()
        self.svc.hint(a.id, "q1")
        self.svc.answer(a.id, "q1", 1)
        self.svc.show_solution(a.id, "q2")
        self.svc.answer(a.id, "q2", "Lists key idea")
        self.svc.finish(a.id)
        ind = {e.note: e.independence for e in self.repo.evidence.values()}
        self.assertEqual(ind, {"multiple_choice": "hint", "explain": "saw_solution"})
        self.assertNotEqual(self.lists.state, "understood")

    def test_unanswered_items_are_no_penalty(self):
        a = self.quiz()
        result = self.svc.finish(a.id)
        self.assertEqual((result["answered"], self.lists.state, self.lists.score), (0, "new", None))

    def test_skipping_costs_nothing(self):
        a = self.quiz()
        before = (self.lists.state, self.lists.score, len(self.repo.evidence))
        result = self.svc.skip(a.id)
        self.assertEqual((self.lists.state, self.lists.score, len(self.repo.evidence)), before)
        self.assertEqual(result["status"], "skipped")

    def test_finished_activity_cannot_be_answered_again(self):
        a = self.quiz()
        self.svc.finish(a.id)
        with self.assertRaises(ActivityError):
            self.svc.answer(a.id, "q1", 1)


class TestUnfinishedQuiz(Base):
    def test_two_days_then_red_indicator_next_to_subject_only(self):
        a = self.quiz()
        self.clock.now = "2026-11-25T10:00:00+00:00"
        self.assertEqual(self.svc.recommend()["action"], "continue")
        self.assertEqual(self.svc.subject_indicators(), {})
        self.clock.now = "2026-11-26T10:00:01+00:00"
        self.assertEqual(self.svc.subject_indicators(), {self.subject.id: "red"})
        self.assertEqual(self.repo.activities[a.id].status, "expired")
        self.assertEqual(self.lists.state, "new")                 # no penalty
        self.assertNotEqual(self.svc.recommend()["action"], "continue")


class TestRecommendation(Base):
    def test_new_topic_gets_a_short_quiz(self):
        rec = self.svc.recommend()
        self.assertEqual((rec["activity_type"], rec["purpose"]), ("quiz", "learn"))

    def test_missing_prerequisite_comes_first(self):
        loops = self.repo.add_topic(self.subject.id, "Loops", "seen", "autonomous")
        self.repo.add_edge(Edge(loops.id, self.lists.id, "prerequisite"), "t", "autonomous")
        self.lists.state = "reinforce"
        rec = self.svc.recommend()
        self.assertEqual(rec["topic_id"], loops.id)
        self.assertTrue(rec["foundation"])
        self.assertIn("בסיס", rec["reason"])

    def test_reinforce_with_weak_score_gets_targeted_quiz_else_game(self):
        self.lists.state, self.lists.score = "reinforce", 0.2
        self.assertEqual(self.svc.recommend()["activity_type"], "quiz")
        self.lists.score = 0.6
        self.assertEqual(self.svc.recommend()["activity_type"], "game")

    def test_understood_topic_gets_retention_check_after_time(self):
        self.lists.state = "understood"
        self.repo.add_evidence(Evidence("ev_x", self.lists.id, "quiz", "user", "c", True, score=1.0,
                                        occurred_at="2026-10-01T10:00:00+00:00"), "t", "autonomous")
        rec = self.svc.recommend()
        self.assertEqual((rec["activity_type"], rec["purpose"]), ("quiz", "retention"))

    def test_fresh_understood_topic_gets_transfer_challenge(self):
        self.lists.state = "understood"
        self.repo.add_evidence(Evidence("ev_y", self.lists.id, "quiz", "user", "c", True, score=1.0,
                                        occurred_at=T0), "t", "autonomous")
        self.assertEqual(self.svc.recommend()["activity_type"], "challenge")


class TestLanguage(Base):
    def test_texts_follow_the_language_setting(self):
        from brain.settings import Settings
        en = ActivityService(self.repo, self.model, ProvisionalMasteryModel(), Settings(language="en"))
        a = en.create(self.lists.id, "quiz")
        en.start(a.id)
        self.assertEqual(en.answer(a.id, "q1", 1)["feedback"], "Correct.")
        self.assertIn("unfinished activity", en.recommend()["reason"])
        he = self.svc.create(self.lists.id, "quiz")
        self.svc.start(he.id)
        self.assertIn("נכונה", he.content["items"][0]["prompt"])


class TestProjectsAndGames(Base):
    def test_project_code_is_marked_ai_and_explanations_are_the_evidence(self):
        a = self.svc.create(self.lists.id, "project", "understand_code")
        view = self.svc.start(a.id)
        self.assertEqual(view["code_author"], "ai")
        for item_id, text in (("p1", "sorts numbers key idea"), ("p2", "order unchanged key idea"),
                              ("p3", "empty list key idea")):
            self.svc.answer(a.id, item_id, text)
        self.svc.finish(a.id)
        types = sorted(e.type for e in self.repo.evidence.values())
        self.assertEqual(types, ["analyze", "debug", "explain"])
        self.assertNotIn("apply", types)                          # no "can write code" claim

    def test_match_game_is_graded_by_pairs(self):
        a = self.quiz(kind="game", purpose="reinforce")
        result = self.svc.answer(a.id, "g1", [["מונח 1", "משמעות 1"], ["מונח 2", "משמעות 3"],
                                              ["מונח 3", "משמעות 2"]])
        self.assertAlmostEqual(result["score"], 0.33)

    def test_retention_quiz_records_retention_evidence(self):
        a = self.quiz(purpose="retention")
        self.svc.answer(a.id, "q1", 1)
        self.svc.finish(a.id)
        self.assertIn("retention", {e.type for e in self.repo.evidence.values()})


if __name__ == "__main__":
    unittest.main()
