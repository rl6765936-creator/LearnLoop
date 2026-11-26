"""B4: understanding assessment. The provisional model is a TEST model – these tests check
the APPROVED principles (guard), the history and the plug-in interface, not a final formula."""

import unittest

from brain.domain.mastery import (BayesianMasteryModel, PlaceholderMasteryModel,
                                  ProvisionalMasteryModel, model_from_settings, reassess,
                                  understood_guard)
from brain.domain.models import Evidence
from brain.domain.repository import InMemoryRepository
from brain.settings import Settings, describe

NOW = "2026-11-24T12:00:00+00:00"


class Base(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryRepository(clock=lambda: NOW)
        s = self.repo.add_subject("Python", "req", "approved")
        self.topic = self.repo.add_topic(s.id, "Lists", "seen", "autonomous")
        self.model = ProvisionalMasteryModel()

    def ev(self, type_, score, independence="independent", user=True, when=NOW):
        e = Evidence(self.repo.new_id("ev"), self.topic.id, type_, "user" if user else "ai", "c0",
                     user, score=score, independence=independence, occurred_at=when,
                     source="activity")
        self.repo.add_evidence(e, "test", "autonomous")
        return e

    def run_model(self):
        return reassess(self.repo, self.model, self.topic, "test", [])


class TestApprovedPrinciples(Base):
    def test_a_single_perfect_quiz_is_never_understood(self):
        self.ev("quiz", 1.0)
        self.run_model()
        self.assertNotEqual(self.topic.state, "understood")

    def test_many_quizzes_of_one_kind_are_not_enough(self):
        for _ in range(5):
            self.ev("quiz", 1.0)
        self.run_model()
        self.assertNotEqual(self.topic.state, "understood")

    def test_varied_independent_evidence_can_reach_understood(self):
        self.ev("quiz", 1.0)
        self.ev("explain", 0.9)
        self.run_model()
        self.assertEqual(self.topic.state, "understood")

    def test_success_only_after_seeing_the_solution_is_not_understood(self):
        self.ev("quiz", 1.0, independence="saw_solution")
        self.ev("explain", 1.0, independence="substantial_help")
        allowed, reason = understood_guard(list(self.repo.evidence.values()), 0.8)
        self.assertFalse(allowed)
        self.assertEqual(reason, "guard.independent")

    def test_ai_generated_work_never_counts(self):
        self.ev("apply", 1.0, user=False)
        self.ev("debug", 1.0, user=False)
        self.run_model()
        self.assertEqual(self.topic.state, "new")
        self.assertIsNone(self.topic.score)

    def test_exposure_alone_means_learning_not_understanding(self):
        self.ev("exposure", None, independence=None)
        self.run_model()
        self.assertEqual((self.topic.state, self.topic.score), ("learning", None))


class TestProvisionalBehaviour(Base):
    def test_help_lowers_the_score(self):
        self.ev("quiz", 1.0, independence="hint")
        self.ev("quiz", 0.0)
        hinted = self.run_model()["after"]["score"]
        self.assertLess(hinted, 0.5)

    def test_low_results_mean_reinforce(self):
        self.ev("quiz", 0.2)
        self.ev("explain", 0.3)
        self.run_model()
        self.assertEqual(self.topic.state, "reinforce")

    def test_understood_topic_without_recent_evidence_needs_retention_check(self):
        old = "2026-10-01T12:00:00+00:00"
        self.ev("quiz", 1.0, when=old)
        self.ev("explain", 1.0, when=old)
        self.topic.state = "understood"
        self.run_model()
        self.assertEqual(self.topic.state, "reinforce")

    def test_every_change_is_recorded_with_reason_and_model(self):
        self.ev("quiz", 1.0)
        e2 = self.ev("explain", 0.9)
        reassess(self.repo, self.model, self.topic, "quiz finished", [e2.id])
        rec = self.repo.history[-1]
        self.assertEqual((rec.action, rec.before["state"], rec.after["state"], rec.after["model"]),
                         ("mastery_updated", "new", "understood", "provisional"))
        from brain.i18n import render
        self.assertIn("[מודל זמני לבדיקה]", render(rec.reason))
        self.assertIn("[provisional test model]", render(rec.reason, "en"))
        self.assertEqual(rec.evidence_ids, [e2.id])

    def test_no_change_no_record(self):
        self.ev("quiz", 0.6)
        self.run_model()
        n = len(self.repo.history)
        self.assertIsNone(self.run_model())
        self.assertEqual(len(self.repo.history), n)

    def test_placeholder_never_changes(self):
        self.ev("quiz", 1.0)
        self.ev("explain", 1.0)
        self.assertIsNone(reassess(self.repo, PlaceholderMasteryModel(), self.topic, "t", []))


OLD = "2026-09-25T12:00:00+00:00"          # 60 days before NOW


class TestApprovedFormula(Base):
    """Every row of the approved table (claude/mastery-formula-proposal.md §5)."""

    def setUp(self):
        super().setUp()
        self.model = BayesianMasteryModel()

    def scenario(self, results, previous="learning"):
        self.topic.state = previous
        for type_, score, ind, when in results:
            self.ev(type_, score, independence=ind, when=when)
        self.run_model()
        return round(self.topic.score, 2), self.topic.state

    def test_1_single_perfect_quiz(self):
        self.assertEqual(self.scenario([("quiz", 1.0, "independent", NOW)]), (0.67, "learning"))

    def test_2_quiz_and_explanation_alone_today(self):
        self.assertEqual(self.scenario([("quiz", 1.0, "independent", NOW), ("explain", 0.9, "independent", NOW)]),
                         (0.74, "understood"))

    def test_3_same_with_hint_and_solution_shown(self):
        self.assertEqual(self.scenario([("quiz", 1.0, "hint", NOW), ("explain", 0.9, "saw_solution", NOW)]),
                         (0.64, "learning"))

    def test_4_three_strong_varied_results(self):
        self.assertEqual(self.scenario([("quiz", 1.0, "independent", NOW), ("explain", 0.9, "independent", NOW),
                                        ("debug", 0.8, "independent", NOW)]), (0.76, "understood"))

    def test_5_two_weak_results(self):
        self.assertEqual(self.scenario([("quiz", 0.2, "independent", NOW), ("explain", 0.3, "independent", NOW)]),
                         (0.37, "reinforce"))

    def test_6_time_alone_does_not_lower_the_state(self):
        old = [("quiz", 1.0, "independent", OLD), ("explain", 0.9, "independent", OLD),
               ("debug", 0.8, "independent", OLD)]
        self.assertEqual(self.scenario(old, previous="understood"), (0.63, "understood"))

    def test_7_failed_retention_check_lowers_it(self):
        results = [("quiz", 1.0, "independent", OLD), ("explain", 0.9, "independent", OLD),
                   ("debug", 0.8, "independent", OLD), ("retention", 0.2, "independent", NOW)]
        self.assertEqual(self.scenario(results, previous="understood"), (0.49, "reinforce"))

    def test_8_five_perfect_quizzes_of_one_kind(self):
        self.assertEqual(self.scenario([("quiz", 1.0, "independent", NOW)] * 5), (0.86, "learning"))

    def test_confidence_is_the_evidence_weight_behind_the_score(self):
        self.scenario([("quiz", 1.0, "independent", NOW), ("explain", 0.9, "independent", NOW)])
        self.assertEqual(self.topic.confidence, 2.5)
        self.assertEqual(self.repo.history[-1].after["confidence"], 2.5)

    def test_one_weaker_result_does_not_undo_understanding(self):
        self.scenario([("quiz", 1.0, "independent", NOW), ("explain", 0.9, "independent", NOW)])
        self.ev("quiz", 0.3)
        self.run_model()
        self.assertEqual(self.topic.state, "understood")

    def test_settings_change_the_formula_without_code(self):
        from brain.settings import MasterySettings
        strict = BayesianMasteryModel(MasterySettings(understood_score=0.8))
        self.ev("quiz", 1.0)
        self.ev("explain", 0.9)
        reassess(self.repo, strict, self.topic, "t", [])
        self.assertEqual(self.topic.state, "learning")

    def test_default_model_is_the_approved_formula(self):
        self.assertEqual(model_from_settings(None).name, "bayesian")


class TestSettings(unittest.TestCase):
    def test_every_setting_carries_a_status(self):
        rows = describe(Settings())
        self.assertTrue(rows)
        self.assertTrue(all(r["status"] for r in rows))

    def test_approved_value_is_marked_approved(self):
        row = next(r for r in describe() if r["setting"] == "activities.unfinished_quiz_days")
        self.assertEqual((row["value"], row["status"]), (2, "APPROVED"))
