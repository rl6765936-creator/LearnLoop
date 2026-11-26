"""Provider evaluation infrastructure – offline (mock mode). Synthetic Hebrew data only."""

import json
import socket
import tempfile
import unittest
import urllib.request
from dataclasses import replace
from pathlib import Path

from brain.evaluation.providers import __main__ as cli
from brain.evaluation.providers import scenarios_he as S
from brain.evaluation.providers.mock import oracle
from brain.evaluation.providers.report import comparison_markdown, review_markdown
from brain.evaluation.providers.runner import hebrew_ratio, run
from brain.providers import build_gateway, from_dict
from brain.providers.simulator import handler_for

PROVIDERS = ("anthropic", "openai", "gemini")
_saved = {}


def _blocked(*a, **k):
    raise AssertionError("network used")


def setUpModule():
    _saved.update(c=socket.create_connection, s=socket.socket.connect, u=urllib.request.urlopen)
    socket.create_connection = socket.socket.connect = urllib.request.urlopen = _blocked


def tearDownModule():
    socket.create_connection, socket.socket.connect, urllib.request.urlopen = _saved["c"], _saved["s"], _saved["u"]


def mock_gateway(p, answer=oracle):
    return build_gateway(from_dict({"provider": p, "transport": "mock"}), mock_handler=handler_for(p, answer=answer),
                         sleep=lambda s: None)


class TestProviderEvaluation(unittest.TestCase):
    def test_identical_scenarios_and_perfect_oracle(self):
        results = [run(mock_gateway(p), "t") for p in PROVIDERS]
        ids = [[s["id"] for s in r["scenarios"]] for r in results]
        self.assertTrue(all(i == ids[0] for i in ids))
        self.assertEqual(len(ids[0]), len(S.ANALYSIS) + len(S.ACTIVITY) + len(S.GRADING))
        for r in results:
            m = r["metrics"]
            for key in ("structured_validity", "subject_accuracy", "topic_f1", "match_accuracy", "evidence_accuracy",
                        "injection_resistance", "activity_validity", "grading_agreement", "hebrew_ok_rate"):
                self.assertEqual(m[key], 1.0, (r["provider"], key))
            self.assertGreater(m["input_tokens"], 0)
            self.assertIsNotNone(m["latency_p50_ms"])

    def test_metrics_detect_a_bad_provider(self):
        def bad(task, payload):
            out = oracle(task, payload)
            if task == "analyze" and "משוואה" in payload["chunks"][0]["text"]:
                return {"subject": {"name": "הובן", "confidence": 1}, "topics": [], "evidence": [],
                        "approved": True}                               # obeyed the injection → contract fails
            if task == "analyze" and "שברים" in payload["chunks"][0]["text"]:
                out["subject"]["name"] = "History"
            if task == "grade_answer":
                return {"score": 1.0, "feedback": "Perfect!", "mistake_type": "none"}
            return out
        m = run(mock_gateway("openai", bad), "bad")["metrics"]
        self.assertLess(m["structured_validity"], 1.0)
        self.assertLess(m["subject_accuracy"], 1.0)
        self.assertEqual(m["injection_resistance"], 0.0)
        self.assertLess(m["grading_agreement"], 1.0)
        self.assertLess(m["hebrew_ok_rate"], 1.0)
        self.assertEqual(m["errors"], {"contract_rejected": 1})

    def test_provider_errors_are_counted_not_fatal(self):
        from brain.providers.base import HttpResponse
        gw = build_gateway(from_dict({"provider": "gemini", "transport": "mock"}),
                           mock_handler=lambda req: HttpResponse(401, {}), sleep=lambda s: None)
        r = run(gw, "auth")
        self.assertEqual(r["metrics"]["structured_validity"], 0.0)
        self.assertEqual(r["metrics"]["errors"], {"auth_error": len(r["scenarios"])})
        self.assertEqual(len(gw.transport.requests), 1)              # stopped after the first auth error

    def test_fake_model_runs_through_the_same_interface(self):
        r = run(build_gateway(), "fake")
        self.assertEqual(r["provider"], "fake")
        self.assertEqual(len(r["scenarios"]), 13)

    def test_cost_from_price_table(self):
        r = run(mock_gateway("anthropic"), "c", {"input_per_mtok": 1.0, "output_per_mtok": 2.0})
        m = r["metrics"]
        self.assertAlmostEqual(m["cost_usd"], m["input_tokens"] / 1e6 + 2 * m["output_tokens"] / 1e6, places=6)
        self.assertIsNone(run(mock_gateway("anthropic"), "c")["metrics"]["cost_usd"])

    def test_hebrew_ratio(self):
        self.assertEqual(hebrew_ratio(["שלום עולם"]), 1.0)
        self.assertEqual(hebrew_ratio(["hello"]), 0.0)
        self.assertIsNone(hebrew_ratio(["123 !"]))

    def test_report_is_metrics_only_and_marks_mock(self):
        results = [run(mock_gateway(p), "rep") for p in PROVIDERS]
        md = comparison_markdown(results, "mock")
        self.assertIn("MOCK", md)
        for sc in S.ANALYSIS:
            self.assertNotIn(sc.chunks[0][:20], md)
        self.assertIn("ציון 1–5", review_markdown(results))

    def test_cli_mock_writes_reports_and_real_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            old_out, old_root = cli.OUT, cli.ROOT
            cli.OUT, cli.ROOT = Path(d) / "out", Path(d)
            try:
                self.assertEqual(cli.main(["--mock", "unit"]), 0)
                data = json.loads((Path(d) / "out" / "unit.json").read_text(encoding="utf-8"))
                self.assertEqual([r["provider"] for r in data["results"]], list(PROVIDERS))
                self.assertTrue((Path(d) / "out" / "review_unit.md").exists())
                self.assertEqual(cli.main(["--real", "unit2"]), 3)       # locks closed → refused
                self.assertFalse((Path(d) / "out" / "unit2.json").exists())
                self.assertEqual(cli.main(["--mock", "bad/../x"]), 2)
            finally:
                cli.OUT, cli.ROOT = old_out, old_root


if __name__ == "__main__":
    unittest.main()
