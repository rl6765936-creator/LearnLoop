"""Tests for the evaluation harness (B8): coverage, privacy of monitoring data, and that a
regression really makes the before/after comparison fail."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from brain import policy
from brain.evaluation import __main__ as cli
from brain.evaluation import dataset, metrics
from brain.evaluation.compare import compare
from brain.evaluation.telemetry import MetadataRecorder, find_leaks
from brain.ingestion import steps

# Spec §46 (APPROVED) scenario list -> scenario id that covers it.
SPEC_46 = {
    "correctly identified material": "correct_material",
    "incorrectly classified material": "misclassified_material",
    "a new subject": "new_subject",
    "an existing topic": "existing_topic",
    "conflicting evidence": "conflicting_evidence",
    "low confidence": "low_confidence",
    "an approval request": "approval_rejected",
    "transfer without permission": "transfer_without_permission",
    "sensitive information": "sensitive_content",
    "processing failure": "processing_failure",
    "duplicate information": "duplicate_information",
    "modified material": "modified_material",
}


class TestCoverage(unittest.TestCase):
    def test_every_spec_46_scenario_is_covered(self):
        ids = {sc.id for sc in dataset.scenarios()}
        missing = [k for k, v in SPEC_46.items() if v not in ids]
        self.assertEqual(missing, [])

    def test_baseline_meets_all_thresholds(self):
        result = metrics.evaluate("t")
        self.assertEqual([s for s in result["scenarios"] if s["failures"]], [])
        passed, reasons = compare(result, result)
        self.assertTrue(passed, reasons)

    def test_recommendation_quality_is_measured(self):
        self.assertEqual(metrics.evaluate("t")["metrics"]["recommendation_quality"], 1.0)

    def test_before_after_old_provisional_model_vs_approved_formula(self):
        from brain.domain.mastery import ProvisionalMasteryModel
        before = metrics.evaluate("before", mastery_model=ProvisionalMasteryModel())
        after = metrics.evaluate("after")
        self.assertLess(before["metrics"]["mastery_quality"], 1.0)
        self.assertEqual(after["metrics"]["mastery_quality"], 1.0)
        self.assertFalse(compare(after, before)[0])          # going back would be a regression
        self.assertTrue(compare(before, after)[0])

    def test_misclassification_is_visible_in_the_metric(self):
        self.assertLess(metrics.evaluate("t")["metrics"]["classification_f1"], 1.0)


class TestTelemetryPrivacy(unittest.TestCase):
    def test_records_contain_only_allow_listed_fields(self):
        rec = MetadataRecorder("run-1")
        rec.set_scenario("lists")
        rec.observe("analyze", 0.5, {"status": "secret text status", "error": "boom: my notes",
                                     "requests": [{"kind": "new_subject", "summary": "Python"}],
                                     "plan": {"topics": [{"name": "Lists"}]}})
        record = rec.export()[0]
        self.assertEqual(set(record), {"run_id", "scenario", "step", "seconds", "status",
                                       "error_category", "request_kinds", "model_attempts"})
        self.assertEqual((record["status"], record["error_category"]), ("other", "other"))
        self.assertEqual(find_leaks([record], ["my notes", "Python", "Lists", "secret text"]), [])

    def test_unsafe_ids_are_replaced(self):
        rec = MetadataRecorder("bad id with spaces/../")
        rec.set_scenario("lists.py contents")
        rec.observe("validate", 0.0, {})
        self.assertEqual((rec.records[0]["run_id"], rec.records[0]["scenario"]), ("run", "unknown"))

    def test_leak_detector_works(self):
        self.assertEqual(find_leaks([{"x": "numbers.sort()"}], ["numbers.sort()"]), ["numbers.sort()"])

    def test_full_run_has_no_content_in_telemetry(self):
        result = metrics.evaluate("t")
        self.assertGreater(result["telemetry_records"], 0)
        self.assertEqual(result["metrics"]["privacy_leaks"], 0)
        blob = json.dumps(result["telemetry"])
        for sc in dataset.scenarios():
            for env in sc.envelopes:
                for chunk in env["chunks"]:
                    self.assertNotIn(chunk["text"], blob)

    def test_saved_result_file_has_no_content(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cli, "OUT_DIR", Path(tmp)), \
                mock.patch("builtins.print"):
            self.assertEqual(cli.main(["check"]), 0)
            saved = (Path(tmp) / "check.json").read_text(encoding="utf-8")
        self.assertNotIn("telemetry\"", saved)
        self.assertNotIn(dataset.LISTS_TEXT, saved)
        self.assertNotIn(dataset.INJECTION, saved)


class TestRegressionsAreCaught(unittest.TestCase):
    def setUp(self):
        self.baseline = metrics.evaluate("base")

    def test_weaker_approval_rule_fails_the_comparison(self):
        with mock.patch.object(policy, "LOW_CONFIDENCE", 0.0):   # "never ask when unsure"
            candidate = metrics.evaluate("weak")
        self.assertLess(candidate["metrics"]["hitl_accuracy"], 1.0)
        passed, reasons = compare(self.baseline, candidate)
        self.assertFalse(passed)
        self.assertTrue(any("hitl_accuracy" in r for r in reasons))

    def test_writing_before_the_decision_fails_the_comparison(self):
        real_plan = steps.plan

        def plan_that_writes(state, deps):
            deps.repo.record("topic", "x", "created", None, {}, "too early", "autonomous")
            return real_plan(state, deps)

        with mock.patch.object(steps, "plan", plan_that_writes):
            candidate = metrics.evaluate("early-write")
        self.assertGreater(candidate["metrics"]["writes_before_decision"], 0)
        self.assertFalse(compare(self.baseline, candidate)[0])

    def test_classification_drop_beyond_threshold_fails(self):
        worse = json.loads(json.dumps(self.baseline))
        worse["metrics"]["classification_f1"] -= 0.05
        self.assertFalse(compare(self.baseline, worse)[0])
        slightly = json.loads(json.dumps(self.baseline))
        slightly["metrics"]["classification_f1"] -= 0.01
        self.assertTrue(compare(self.baseline, slightly)[0])

    def test_any_privacy_leak_fails(self):
        leaky = json.loads(json.dumps(self.baseline))
        leaky["metrics"]["privacy_leaks"] = 1
        self.assertFalse(compare(self.baseline, leaky)[0])


if __name__ == "__main__":
    unittest.main()
