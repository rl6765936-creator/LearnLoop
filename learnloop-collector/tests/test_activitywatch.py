"""ActivityWatch (decision 2026-11-24 Q12): app name + category only; 127.0.0.1 only; own permission."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from collector.config import load_config
from collector.sources import activitywatch as aw

SECRET_TITLE = "Inbox - private@mail.example - Gmail"


class FakeApi:
    """Pretends to be ActivityWatch. It even returns titles/urls (as a broken server might)."""

    def __init__(self, classes=None, events=None):
        self.calls = []
        self.classes = classes
        self.events = events if events is not None else [
            {"duration": 3600.4, "data": {"app": "Code.exe", "title": "lists.py – secret", "$category": ["Work", "Programming"]}},
            {"duration": 1800, "data": {"app": "chrome.exe", "title": SECRET_TITLE, "url": "https://x", "$category": ["Uncategorized"]}},
            {"duration": 30, "data": {"app": "calc.exe", "title": "calc"}},
            {"duration": 600, "data": {"app": "Code.exe", "title": "other", "$category": ["Work", "Programming"]}},
            {"duration": 99, "data": {"title": "no app"}},
        ]

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/0/buckets/":
            return {"aw-watcher-window_HOST-NAMEשם": {}, "aw-watcher-afk_HOST-NAMEשם": {}}
        if path == "/api/0/settings/classes":
            return self.classes
        if path == "/api/0/query/":
            return [self.events]
        raise AssertionError(path)


class TestActivityWatch(unittest.TestCase):
    def test_only_app_and_category_survive(self):
        api = FakeApi(classes=[{"name": ["Work", "Programming"], "rule": {"type": "regex", "regex": "Code"}}])
        usage = aw.day_usage(date(2026, 11, 23), api)
        self.assertEqual([(u.app, u.category, u.seconds) for u in usage],
                         [("Code.exe", "Work > Programming", 4200), ("chrome.exe", None, 1800)])
        env = aw.summary_envelope(date(2026, 11, 23), usage, "aw-1")
        text = json.dumps(env)
        for leaked in ("title", "url", SECRET_TITLE, "HOST-NAME", "secret", "calc.exe"):
            self.assertNotIn(leaked, text)

    def test_query_merges_on_activitywatch_side(self):
        api = FakeApi(classes=[{"name": ["Study"], "rule": {"type": "regex", "regex": "x"}}])
        aw.day_usage(date(2026, 11, 23), api)
        q = next(b for m, p, b in api.calls if p == "/api/0/query/")
        joined = "\n".join(q["query"])
        self.assertIn('merge_events_by_keys(events, ["app", "$category"])', joined)
        self.assertIn('"not-afk"', joined)
        self.assertEqual(len(q["timeperiods"]), 1)
        self.assertIn('"aw-watcher-window_HOST-NAMEשם"', joined)            # real letters, no \u escapes
        self.assertNotIn("\\u05e9", joined)

    def test_without_category_rules(self):
        api = FakeApi(classes=None)
        aw.day_usage(date(2026, 11, 23), api)
        q = next(b for m, p, b in api.calls if p == "/api/0/query/")
        self.assertIn('merge_events_by_keys(events, ["app"])', "\n".join(q["query"]))

    def test_local_only_and_not_running(self):
        with self.assertRaises(ValueError):
            aw.LocalApi(port=80)
        with self.assertRaises(aw.ActivityWatchUnavailable):
            aw.LocalApi(port=65001, timeout_s=1).call("GET", "/api/0/info")
        self.assertEqual(aw.HOST, "127.0.0.1")

    def test_contract(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not a Collector dependency; the brain checks the contract")
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "activity_summary.schema.json")
                            .read_text(encoding="utf-8"))
        env = aw.summary_envelope(date(2026, 11, 23), aw.day_usage(date(2026, 11, 23), FakeApi()), "aw-1")
        jsonschema.validate(env, schema)
        env["apps"][0]["title"] = "x"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(env, schema)

    def test_permission_config(self):
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text(json.dumps({"activitywatch": {"grant_id": "aw-123"}}), encoding="utf-8")
        self.assertEqual(load_config(p).config.activitywatch, {"grant_id": "aw-123"})
        p.write_text(json.dumps({"activitywatch": {"grant_id": "aw-1", "titles": True}}), encoding="utf-8")
        self.assertIsNone(load_config(p).config.activitywatch)       # unknown options refused (safe default)




class TestActivityWatchErrors(unittest.TestCase):
    def test_refusal_is_reported_not_called_offline(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(400); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"message": "There\'s no bucket named \'aw-watcher-window_\\u05e8\\u05d9x\' categorize עברית"}).encode())

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        with self.assertRaises(aw.ActivityWatchError) as ctx:
            aw.LocalApi(port=srv.server_address[1]).call("GET", "/api/0/buckets/")
        self.assertIn("categorize", str(ctx.exception))
        self.assertNotRegex(str(ctx.exception), "[֐-׿]")


if __name__ == "__main__":
    unittest.main()
