import os
import unittest
from unittest import mock

from brain.evaluation import metrics
from brain.evaluation.langsmith_adapter import LangSmithExporter, sanitize
from brain.evaluation.telemetry import find_leaks
from brain.evaluation import dataset


class FakeClient:
    def __init__(self):
        self.runs = []

    def create_run(self, **kwargs):
        self.runs.append(kwargs)


class TestLangSmithAdapter(unittest.TestCase):
    def test_off_by_default_sends_nothing(self):
        self.assertEqual(LangSmithExporter().export([{"step": "plan"}]), 0)

    def test_enabled_without_api_key_sends_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(LangSmithExporter(enabled=True).export(metrics.evaluate("x")["telemetry"]), 0)

    def test_full_evaluation_run_sends_metadata_only(self):
        client = FakeClient()
        records = metrics.evaluate("ls-check")["telemetry"]
        sent = LangSmithExporter(enabled=True, client=client).export(records)
        self.assertEqual(sent, len(records))
        for run in client.runs:
            self.assertEqual((run["inputs"], run["outputs"]), ({}, {}))
            self.assertTrue(run["name"].startswith("learnloop."))
        forbidden = [c["text"] for sc in dataset.scenarios() for e in sc.envelopes for c in e["chunks"]]
        forbidden += [e["file"]["rel_path"] for sc in dataset.scenarios() for e in sc.envelopes]
        self.assertEqual(find_leaks(client.runs, forbidden), [])

    def test_unsafe_records_are_dropped_or_stripped(self):
        good = {"run_id": "r", "scenario": "s", "step": "plan", "seconds": 0.1, "status": "routine",
                "error_category": None, "request_kinds": [], "model_attempts": 0}
        self.assertEqual(sanitize(dict(good, prompt="the full prompt")), good)        # extra field stripped
        self.assertIsNone(sanitize(dict(good, step="my file lists.py")))
        self.assertIsNone(sanitize(dict(good, scenario="Lists of Python topics")))
        self.assertIsNone(sanitize(dict(good, request_kinds=["subject: Python"])))
        client = FakeClient()
        exporter = LangSmithExporter(enabled=True, client=client)
        self.assertEqual(exporter.export([dict(good, status="free text")]), 0)
        self.assertEqual(exporter.dropped, 1)


if __name__ == "__main__":
    unittest.main()
