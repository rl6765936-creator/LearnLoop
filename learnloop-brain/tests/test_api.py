"""B9 backend: routes over the app, and the local server's safety rules."""

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from brain.api.routes import handle
from brain.api.server import make_handler
from brain.demo import build_demo_app

NOW = "2026-11-24T10:00:00+00:00"


class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.app = build_demo_app(clock=lambda: NOW)

    def test_full_flow_through_routes(self):
        status, body = handle(self.app, "GET", "/api/approvals")
        python = next(a for a in body["approvals"] if "Python" in a["summary"])
        self.assertEqual(handle(self.app, "POST", f"/api/approvals/{python['id']}", {"action": "approve"})[0], 200)
        subjects = handle(self.app, "GET", "/api/subjects")[1]["subjects"]
        sid = subjects[0]["id"]
        nxt = handle(self.app, "GET", f"/api/subjects/{sid}/next")[1]["next"]
        status, activity = handle(self.app, "POST", "/api/activities",
                                  {"topic_id": nxt["topic_id"], "activity_type": nxt["activity_type"],
                                   "purpose": nxt["purpose"]})
        self.assertEqual(status, 200)
        aid = activity["id"]
        self.assertEqual(handle(self.app, "POST", f"/api/activities/{aid}/answer",
                                {"item_id": "q1", "answer": 1})[1]["score"], 1.0)
        result = handle(self.app, "POST", f"/api/activities/{aid}/finish")[1]
        self.assertEqual(result["answered"], 1)
        dash = handle(self.app, "GET", f"/api/subjects/{sid}/dashboard")[1]
        self.assertEqual(dash["assessment_model"], "bayesian")

    def test_errors_are_short_and_safe(self):
        self.assertEqual(handle(self.app, "GET", "/api/nothing")[0], 404)
        self.assertEqual(handle(self.app, "GET", "/api/subjects/nope/dashboard")[0], 404)
        status, body = handle(self.app, "POST", "/api/activities/act_x/finish")
        self.assertEqual(status, 400)
        self.assertNotIn("Traceback", json.dumps(body))

    def test_settings_show_status_labels(self):
        rows = handle(self.app, "GET", "/api/settings")[1]["settings"]
        self.assertTrue(all("status" in r for r in rows))


class TestServerSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(build_demo_app(clock=lambda: NOW)))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        con.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
        res = con.getresponse()
        data = res.read()
        con.close()
        return res.status, data, res

    def test_ui_and_api_are_served(self):
        status, data, res = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"LearnLoop", data)
        self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))
        self.assertEqual(self.request("GET", "/api/subjects")[0], 200)

    def test_post_without_the_custom_header_is_refused(self):
        self.assertEqual(self.request("POST", "/api/cycle", {})[0], 403)
        self.assertEqual(self.request("POST", "/api/cycle", {}, {"X-LearnLoop": "1"})[0], 200)

    def test_foreign_host_header_is_refused(self):
        self.assertEqual(self.request("GET", "/api/subjects", headers={"Host": "evil.example"})[0], 403)

    def test_no_files_outside_the_ui_folder(self):
        self.assertEqual(self.request("GET", "/../src/brain/app.py")[0], 404)
        self.assertEqual(self.request("GET", "/%2e%2e/README.md")[0], 404)


if __name__ == "__main__":
    unittest.main()
