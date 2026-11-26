"""Decisions 2026-11-24: Q8 (brain on her computer, key only on the server side),
Q10 (classification answers go back to the Collector, no material), Q16 (DPAPI + recovery code)."""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from brain.api.server import make_handler
from brain.demo import build_demo_app
from brain.storage.keystore import KeyStore, KeyStoreError, new_recovery_code
from helpers import envelope

NOW = "2026-11-24T10:00:00+00:00"
TOKEN = "local-test-token-0123456789"


class FakeProtector:
    """Stands in for Windows DPAPI in the cloud tests: reversible, tied to one 'user'."""

    def __init__(self, user=b"u1"):
        self.user = user

    def protect(self, data):
        return self.user + bytes(b ^ 0x5A for b in data)

    def unprotect(self, data):
        if not data.startswith(self.user):
            raise KeyStoreError("Windows could not protect/unprotect the key")
        return bytes(b ^ 0x5A for b in data[len(self.user):])


class TestKeyStore(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_create_load_and_code_not_on_disk(self):
        ks = KeyStore(self.dir, FakeProtector())
        key, code = ks.create()
        self.assertEqual(len(key), 32)
        self.assertEqual(ks.load(), key)
        for f in self.dir.iterdir():
            data = f.read_bytes()
            self.assertNotIn(code.encode(), data)
            self.assertNotIn(code.replace("-", "").encode(), data)
            self.assertNotIn(key, data)

    def test_refuses_to_overwrite(self):
        ks = KeyStore(self.dir, FakeProtector())
        ks.create()
        with self.assertRaises(KeyStoreError):
            ks.create()

    def test_recover_on_new_windows_user(self):
        key, code = KeyStore(self.dir, FakeProtector(b"old")).create()
        moved = KeyStore(self.dir, FakeProtector(b"new"))
        with self.assertRaises(KeyStoreError):
            moved.load()                       # other Windows user cannot open key.dpapi
        self.assertEqual(moved.recover(code.lower().replace("-", " ")), key)
        self.assertEqual(moved.load(), key)

    def test_wrong_code(self):
        _, code = KeyStore(self.dir, FakeProtector()).create()
        with self.assertRaises(KeyStoreError):
            KeyStore(self.dir, FakeProtector()).recover(new_recovery_code())

    def test_code_shape(self):
        parts = new_recovery_code().split("-")
        self.assertEqual(len(parts), 6)
        self.assertTrue(all(len(p) == 5 for p in parts))


class TestLocalCollectorLink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = build_demo_app(clock=lambda: NOW)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app, ingest_token=TOKEN))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body, token=TOKEN):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "X-LearnLoop": "1"}
        if token is not None:
            headers["X-LearnLoop-Token"] = token
        conn.request("POST", path, data, headers)
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_ingest_needs_the_local_token(self):
        env = envelope(["def f(): return [1, 2]"], rel_path="link/a.py", source="src_link", grant="gA")
        self.assertEqual(self.post("/api/ingest", env, token=None)[0], 403)   # the browser UI has no token
        self.assertEqual(self.post("/api/ingest", env, token="wrong-token-xxxxxxxxxx")[0], 403)
        status, ack = self.post("/api/ingest", env)
        self.assertEqual(status, 200)
        self.assertTrue(ack.get("accepted"), ack)
        self.assertNotIn("def f", json.dumps(ack))                            # answers carry no content

    def test_revoke_then_new_grant(self):
        env = envelope(["x = 1"], rel_path="link/b.py", source="src_rev", grant="g1")
        self.assertEqual(self.post("/api/ingest", env)[0], 200)
        self.assertEqual(self.post("/api/ingest/revoke", {"source_id": "src_rev"})[0], 200)
        self.assertNotIn("src_rev", self.app.collector_grants)

    def test_classifications_feed_has_no_material(self):
        status, body = self.post("/api/ingest/classifications", {})
        self.assertEqual(status, 200)
        for row in body["decided"]:
            self.assertEqual(set(row), {"source_id", "collection", "study_status", "period"})

    def test_bad_json(self):
        status, ack = self.post("/api/ingest", b"{not json")
        self.assertEqual(status, 200)
        self.assertFalse(ack["accepted"])



@unittest.skipUnless(__import__("sys").platform == "win32", "real Windows DPAPI – runs on her computer only")
class TestRealDpapi(unittest.TestCase):
    def test_round_trip_and_recovery(self):
        from brain.storage.keystore import DpapiProtector
        d = Path(tempfile.mkdtemp())
        ks = KeyStore(d, DpapiProtector())
        key, code = ks.create()
        self.assertEqual(KeyStore(d, DpapiProtector()).load(), key)
        self.assertEqual(KeyStore(d, DpapiProtector()).recover(code), key)


if __name__ == "__main__":
    unittest.main()
