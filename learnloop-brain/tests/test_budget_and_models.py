"""Decisions 2026-11-24 (evening): OpenAI models, monthly AI budget, key never in the UI,
finished / not-studied material stays 'new'. Offline – synthetic data, mock provider."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from brain.api.routes import handle
from brain.app import LearnLoopApp
from brain.model_gateway import FakeModel
from brain.providers import build_gateway, from_dict
from brain.providers.base import ConfigError
from brain.providers.budget import BudgetExhausted, BudgetLedger
from brain.providers.config import DEFAULT_PRICES
from brain.providers.simulator import handler_for, wrap_openai
from helpers import SOURCE, analysis, envelope

NOV = lambda: datetime(2026, 11, 24, tzinfo=timezone.utc)          # noqa: E731
DEC = lambda: datetime(2026, 12, 1, tzinfo=timezone.utc)           # noqa: E731
KEY = "sk-proj-THIS-IS-A-TEST-KEY-0000000000"


def openai_gateway(ledger, script=None, **cfg):
    conf = {"provider": "openai", "transport": "mock", "models": {"openai": "gpt-6-luna"}}
    conf.update(cfg)
    return build_gateway(from_dict(conf), mock_handler=handler_for("openai", script or analysis()),
                         budget=ledger, sleep=lambda s: None)


class TestModels(unittest.TestCase):
    def test_luna_default_terra_only_per_task_sol_refused(self):
        gw = openai_gateway(None, task_models={"generate_activity": "gpt-6.1-terra"})
        gw.analyze([{"chunk_id": "c0", "text": "x"}], [])
        gw.generate_activity({"type": "quiz", "purpose": "learn", "subject": "S", "language": "he", "max_items": 3,
                              "topics": [{"name": "T", "state": "new"}]})
        models = [r.body["model"] for r in gw.transport.requests]
        self.assertEqual(models, ["gpt-6-luna", "gpt-6.1-terra"])
        for bad in ({"models": {"openai": "gpt-6.2-sol"}}, {"task_models": {"analyze": "gpt-6.2-sol"}},
                    {"task_models": {"everything": "gpt-6-luna"}}):
            conf = {"provider": "openai", "models": {"openai": "gpt-6-luna"}}
            conf.update(bad)
            with self.assertRaises(ConfigError):
                from_dict(conf)


class TestBudget(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "ai_budget.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_cost_is_checked_before_and_recorded_after(self):
        ledger = BudgetLedger(self.path, 0.80, DEFAULT_PRICES, clock=NOV)
        gw = openai_gateway(ledger)
        gw.analyze([{"chunk_id": "c0", "text": "שלום"}], [])
        self.assertGreater(ledger.spent(), 0)
        self.assertLess(ledger.spent(), 0.001)                     # gpt-6-luna: fractions of a cent
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"2026-11"})
        self.assertNotIn("שלום", self.path.read_text(encoding="utf-8"))   # numbers only

    def test_nothing_is_sent_when_the_budget_would_be_exceeded(self):
        ledger = BudgetLedger(self.path, 0.0005, DEFAULT_PRICES, clock=NOV)
        gw = openai_gateway(ledger)
        with self.assertRaises(BudgetExhausted):
            gw.analyze([{"chunk_id": "c0", "text": "א" * 5000}], [])
        self.assertEqual(gw.transport.requests, [])

    def test_unknown_price_is_refused_not_guessed(self):
        ledger = BudgetLedger(self.path, 0.80, {}, clock=NOV)
        gw = openai_gateway(ledger)
        with self.assertRaises(BudgetExhausted):
            gw.analyze([{"chunk_id": "c0", "text": "x"}], [])
        self.assertEqual(gw.transport.requests, [])

    def test_new_month_new_budget_and_corrupt_ledger_fails_closed(self):
        self.path.write_text(json.dumps({"2026-11": {"usd": 0.80, "requests": 9}}), encoding="utf-8")
        self.assertTrue(BudgetLedger(self.path, 0.80, DEFAULT_PRICES, clock=NOV).exhausted())
        self.assertFalse(BudgetLedger(self.path, 0.80, DEFAULT_PRICES, clock=DEC).exhausted())
        self.path.write_text("{broken", encoding="utf-8")
        self.assertTrue(BudgetLedger(self.path, 0.80, DEFAULT_PRICES, clock=NOV).exhausted())

    def test_exhausted_budget_pauses_ai_and_keeps_the_queue(self):
        self.path.write_text(json.dumps({"2026-11": {"usd": 0.80, "requests": 9}}), encoding="utf-8")
        ledger = BudgetLedger(self.path, 0.80, DEFAULT_PRICES, clock=NOV)
        app = LearnLoopApp(openai_gateway(ledger), engine="plain")
        app.set_transfer_permission(SOURCE, True)
        app.receive(envelope(["numbers.sort()"]))
        result = app.run_cycle()
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(len(app.ingestion.inbox), 1)                 # nothing lost
        self.assertEqual(app.supervisor.deps.model.transport.requests, [])
        status, body = handle(app, "GET", "/api/ai-budget")
        self.assertTrue(body["budget"]["exhausted"])


class TestKeyNeverReachesTheBrowser(unittest.TestCase):
    def test_no_api_response_contains_the_key(self):
        from brain.providers.adapters import OpenAIAdapter
        from brain.providers.gateway import ProviderModelGateway
        from brain.providers.transport import NetworkTransport
        os.environ["LL_TEST_OPENAI"] = KEY

        def opener(req, timeout, context=None):
            raise AssertionError("no network in this test")
        try:
            gw = ProviderModelGateway(OpenAIAdapter("gpt-6-luna"), NetworkTransport("LL_TEST_OPENAI", _opener=opener))
            app = LearnLoopApp(gw, engine="plain")
            blob = ""
            for path in ("/api/subjects", "/api/approvals", "/api/next", "/api/log", "/api/settings",
                         "/api/classifications", "/api/schedule", "/api/strengths", "/api/ai-budget"):
                status, body = handle(app, "GET", path)
                blob += json.dumps(body)
            self.assertNotIn(KEY, blob)
            self.assertNotIn("sk-proj", blob)
        finally:
            os.environ.pop("LL_TEST_OPENAI")


class TestFinishedMaterialStaysNew(unittest.TestCase):
    def setUp(self):
        self.app = LearnLoopApp(FakeModel(analysis(subject="C#", topics=(("Stack", 0.9),))), engine="plain",
                                clock=lambda: "2026-11-24T10:00:00+00:00")
        self.app.set_transfer_permission(SOURCE, True)

    def ingest(self, status, collection="y1"):
        e = envelope(["Stack"], rel_path=f"{collection}/s.md")
        e["context"] = {"collection": collection, "study_status": status, "period": None, "queue": "backfill",
                        "modified_at": "2025-01-01T00:00:00+00:00"}
        self.app.receive(e)
        self.app.run_cycle()
        for p in self.app.pending_approvals():
            self.app.decide(p["id"], "approve")
        return next(iter(self.app.repo.topics.values()))

    def test_completed_and_not_studied_stay_new_active_moves_to_learning(self):
        for status, expected in (("completed", "new"), ("not_studied", "new")):
            self.setUp()
            self.assertEqual(self.ingest(status).state, expected, status)
        self.setUp()
        self.assertEqual(self.ingest("active").state, "learning")

    def test_unknown_then_classified_finished_goes_back_to_no_evidence(self):
        topic = self.ingest("unknown")
        self.assertEqual(topic.state, "learning")                    # approved rule for unknown material
        qid = self.app.pending_classifications()[0]["items"][0]["id"]
        self.app.classify(qid, "completed")
        self.assertEqual(topic.state, "new")
        last = [r for r in self.app.repo.history if r.entity == "topic"][-1]
        self.assertEqual(last.after["state"], "new")

    def test_her_answers_still_count_for_finished_material(self):
        topic = self.ingest("completed")
        act = self.app.start_activity(topic.id, "quiz", "learn")
        self.app.answer(act["id"], "q1", 1)
        self.app.finish_activity(act["id"])
        self.assertNotEqual(topic.state, "new")


if __name__ == "__main__":
    unittest.main()


class TestFirstMonthBudget(unittest.TestCase):
    """Decision 2026-11-25 (option 1): $5 for the first month of real material, then $0.80."""

    def test_override_only_for_that_month(self):
        from datetime import datetime, timezone
        from brain.providers.budget import BudgetLedger, BudgetExhausted
        from brain.providers.config import DEFAULT_PRICES
        now = [datetime(2026, 12, 3, tzinfo=timezone.utc)]
        led = BudgetLedger(None, 0.80, DEFAULT_PRICES, clock=lambda: now[0], month_overrides={"2026-12": 5.0})
        self.assertEqual(led.limit, 5.0)
        led.record("gpt-6-luna", 0, 0, 4.0)
        now[0] = datetime(2027, 1, 2, tzinfo=timezone.utc)
        self.assertEqual(led.limit, 0.80)
        with self.assertRaises(BudgetExhausted):
            led.check("gpt-6-luna", 50_000_000, 1000)

    def test_override_rules(self):
        from brain.providers.config import ProviderConfig, ConfigError
        for bad in ({"2026-12": 6.0}, {"2026-12": 5.0, "2027-01": 5.0}, {"december": 5.0}):
            with self.assertRaises(ConfigError):
                ProviderConfig(budget_month_overrides=bad).check()


class TestCreditExhausted(unittest.TestCase):
    """Prepaid OpenAI credit used up: no retries, AI pauses, queue kept, clear message."""

    def test_insufficient_quota_pauses_without_retrying(self):
        import io
        import urllib.error
        from brain.providers.adapters import OpenAIAdapter
        from brain.providers.gateway import ProviderModelGateway
        from brain.providers.transport import NetworkTransport
        from brain.providers.base import CreditExhausted
        from helpers import envelope
        os.environ["LL_TEST_OPENAI"] = KEY
        calls = []

        def opener(req, timeout, context=None):
            calls.append(1)
            body = json.dumps({"error": {"message": "You exceeded your current quota.", "code": "insufficient_quota"}})
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(body.encode()))
        try:
            now = [1000.0]
            gw = ProviderModelGateway(OpenAIAdapter("gpt-6-luna"), NetworkTransport("LL_TEST_OPENAI", _opener=opener),
                                      clock=lambda: now[0], sleep=lambda s: None)
            with self.assertRaises(CreditExhausted):
                gw.analyze([{"chunk_id": "c1", "location": "x", "text": "hello"}], [])
            self.assertEqual(len(calls), 1)                         # not retried
            self.assertTrue(gw.budget_exhausted() and gw.credit_exhausted())
            app = LearnLoopApp(gw, engine="plain")
            app.set_transfer_permission("s1", True)
            app.receive(envelope(["x = 1"], source="s1"))
            out = app.run_cycle()
            self.assertTrue(out["budget_exhausted"])
            self.assertIn("קרדיט", out["summary"])
            self.assertEqual(out["waiting"], 1)                     # queue kept
            self.assertEqual(len(calls), 1)                         # nothing sent while paused
            self.assertTrue(handle(app, "GET", "/api/system")[1]["ai"]["credit_exhausted"])
            now[0] += 7 * 3600
            self.assertFalse(gw.budget_exhausted())                 # after 6 h one try is allowed again
        finally:
            os.environ.pop("LL_TEST_OPENAI")
