"""Local brain link (decisions 2026-11-24, Q8/Q10): 127.0.0.1 only; classification sync never
overwrites her own Collector settings and never guesses on mixed answers."""

import json
import tempfile
import unittest
from pathlib import Path

from collector.config import load_config
from collector.transfer.brain_link import LocalBrainTransport, plan_sync
from collector.transfer.transports import TransportUnavailable

TOKEN = "t" * 43


class TestBrainLink(unittest.TestCase):
    def test_local_only(self):
        for host in ("localhost.evil.com", "0.0.0.0", "192.168.1.5", "example.com"):
            with self.assertRaises(ValueError):
                LocalBrainTransport(8765, TOKEN, host=host)
        with self.assertRaises(ValueError):
            LocalBrainTransport(80, TOKEN)
        with self.assertRaises(ValueError):
            LocalBrainTransport(8765, "short")

    def test_brain_not_running_keeps_queue(self):
        with self.assertRaises(TransportUnavailable):
            LocalBrainTransport(65000, TOKEN, timeout_s=1).send(b"{}")

    def test_plan_sync(self):
        rows = [
            {"source_id": "S", "collection": "y1", "study_status": "completed", "period": "א"},
            {"source_id": "S", "collection": "y1", "study_status": "completed", "period": "א"},
            {"source_id": "S", "collection": "mix", "study_status": "completed", "period": None},
            {"source_id": "S", "collection": "mix", "study_status": "active", "period": None},
            {"source_id": "S", "collection": "mine", "study_status": "completed", "period": None},
            {"source_id": "OTHER", "collection": "z", "study_status": "active", "period": None},
            {"source_id": "S", "collection": "", "study_status": "active", "period": None},
        ]
        plan = plan_sync(rows, "S", known={"mine": {"study_status": "active", "period": None}})
        self.assertEqual(plan, {"y1": {"study_status": "completed", "period": "א"}})

    def test_config_validation(self):
        d = Path(tempfile.mkdtemp())
        p = d / "c.json"
        p.write_text(json.dumps({"brain_link": {"port": 8765, "token_file": "x"}}), encoding="utf-8")
        self.assertEqual(load_config(p).config.brain_link, {"port": 8765, "token_file": "x"})
        p.write_text(json.dumps({"brain_link": {"port": "8765", "token_file": "x", "host": "evil"}}), encoding="utf-8")
        result = load_config(p)
        self.assertIsNone(result.config.brain_link)


if __name__ == "__main__":
    unittest.main()
