"""Live mode (Q8, Q16): encrypted local DB with a protected key, material held until an approved
provider exists, and a backup that is ACTUALLY restored on a 'new computer'."""

import tempfile
import unittest
from pathlib import Path

from brain.live import HoldModel, backup, build_live_app, ingest_token, provider_ready, restore
from brain.providers.config import ProviderConfig
from helpers import close_databases_after, envelope
from test_local_link_and_keys import FakeProtector

NOW = "2026-11-24T10:00:00+00:00"


class TestLiveMode(unittest.TestCase):
    def setUp(self):
        close_databases_after(self)
        self.dir = Path(tempfile.mkdtemp())

    def test_not_ready_configs_hold(self):
        close_databases_after(self)
        self.assertEqual(provider_ready(ProviderConfig()), "no_provider")
        free = ProviderConfig(provider="gemini", transport="network", allow_network=True, account_tier="free",
                              models={"gemini": "gemini-3.1-flash-lite"})
        self.assertEqual(provider_ready(free), "free_tier_not_for_real_material")

    def test_material_is_held_persisted_and_survives_restart(self):
        close_databases_after(self)
        app, code = build_live_app(ProviderConfig(), self.dir, FakeProtector(), clock=lambda: NOW)
        self.assertIsNotNone(code)
        self.assertIsInstance(app.supervisor.deps.model, HoldModel)
        ack = app.receive_from_collector(envelope(["SECRET_TEXT_MARKER lists"], rel_path="a/x.py",
                                                  source="s1", grant="g1"))
        self.assertTrue(ack["accepted"], ack)
        out = app.run_cycle("scheduled")
        self.assertEqual(out["held"], "no_provider")
        self.assertEqual(out["waiting"], 1)
        app.commit()
        raw = (self.dir / "learnloop.db").read_bytes()
        self.assertNotIn(b"SECRET_TEXT_MARKER", raw)                 # encrypted at rest
        again, code2 = build_live_app(ProviderConfig(), self.dir, FakeProtector(), clock=lambda: NOW)
        self.assertIsNone(code2)                                      # code shown only once
        self.assertEqual(len(again.ingestion.inbox), 1)               # nothing lost

    def test_backup_restore_on_new_computer(self):
        close_databases_after(self)
        app, code = build_live_app(ProviderConfig(), self.dir, FakeProtector(b"old"), clock=lambda: NOW)
        app.receive_from_collector(envelope(["MARK_B"], rel_path="b.py", source="s1", grant="g1"))
        from brain.live import open_key
        key, _ = open_key(self.dir, FakeProtector(b"old"))
        bfile = self.dir / "backup.llb"
        info = backup(app, key, bfile, directory=self.dir)
        self.assertNotIn(b"MARK_B", bfile.read_bytes())
        self.assertEqual(info["recovery_file"], "backup.llb.recovery")
        # new computer: only the backup file, the key folder copy and the paper code
        new = Path(tempfile.mkdtemp())
        from brain.live import install_recovery_file
        install_recovery_file(bfile, new)
        key2, _ = open_key(new, FakeProtector(b"new"), recovery_code=code)
        self.assertEqual(key2, key)
        restore(bfile, key2, new)
        with self.assertRaises(FileExistsError):
            restore(bfile, key2, new)                                 # never over an existing DB
        restored, _ = build_live_app(ProviderConfig(), new, FakeProtector(b"new"), clock=lambda: NOW)
        self.assertEqual(len(restored.ingestion.inbox), 1)
        self.assertIn("MARK_B", restored.ingestion.inbox[0]["chunks"][0]["text"])

    def test_token_is_stable_and_random(self):
        close_databases_after(self)
        t1 = ingest_token(self.dir)
        self.assertEqual(ingest_token(self.dir), t1)
        self.assertGreaterEqual(len(t1), 40)


if __name__ == "__main__":
    unittest.main()


class TestLiveServerThreads(unittest.TestCase):
    """Found on her computer (25.11): the live server answers in other threads than the one that
    opened the database, and /api/system asked the HoldModel for a budget."""

    def test_ingest_and_system_over_real_http(self):
        import http.client
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from brain.api.server import make_handler
        close_databases_after(self)
        d = Path(tempfile.mkdtemp())
        app, _ = build_live_app(ProviderConfig(), d, FakeProtector(), clock=lambda: NOW)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, ingest_token="t" * 40))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def call(method, path, body=None, token=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h = {"X-LearnLoop": "1", "Content-Type": "application/json"}
            if token:
                h["X-LearnLoop-Token"] = token
            c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
            r = c.getresponse()
            out = (r.status, json.loads(r.read() or b"{}"))
            c.close()
            return out
        env = envelope(["x = 1"], rel_path="a/x.py", source="s1", grant="g1")
        status, ack = call("POST", "/api/ingest", env, token="t" * 40)
        self.assertEqual((status, ack["accepted"]), (200, True))
        status, body = call("GET", "/api/system")
        self.assertEqual(status, 200)
        self.assertEqual((body["mode"], body["ai"]["state"], body["ai"]["budget"]), ("live", "held", None))
        self.assertEqual(body["queue"]["waiting"], 1)


class TestResumeAfterCrash(unittest.TestCase):
    """Backlog 'resume after interruption': a cycle cut in the middle (power off, Ctrl+C, crash)
    loses nothing and does not double-count. The waiting material lives in the encrypted DB and
    is only removed by the commit that follows a finished cycle."""

    def test_cut_cycle_is_redone_once(self):
        from brain.model_gateway import FakeModel
        from brain.app import LearnLoopApp
        from helpers import analysis
        close_databases_after(self)
        d = Path(tempfile.mkdtemp())

        class PowerCut(BaseException):
            pass

        calls = {"n": 0}

        def cut(chunks, known):
            calls["n"] += 1
            if calls["n"] == 2:
                raise PowerCut()                       # the second file: the process dies here
            return analysis(subject="Python", topics=(("Lists", 0.9),))

        app, _ = build_live_app(ProviderConfig(), d, FakeProtector(), clock=lambda: NOW)
        app.supervisor.deps.model = FakeModel(cut)
        app.supervisor.deps.model.hold_reason = lambda: None
        for i in range(3):
            app.receive_from_collector(envelope([f"nums{i} = [3, 1]"], rel_path=f"a/f{i}.py", source="s1", grant="g1"))
        app.commit()
        with self.assertRaises(PowerCut):
            app.run_cycle()
        # restart: a NEW process opens the same encrypted database
        again, _ = build_live_app(ProviderConfig(), d, FakeProtector(), clock=lambda: NOW)
        self.assertEqual(len(again.ingestion.inbox), 3)                     # nothing lost
        again.supervisor.deps.model = FakeModel(lambda c, k: analysis(subject="Python", topics=(("Lists", 0.9),)))
        again.supervisor.deps.model.hold_reason = lambda: None
        again.run_cycle()
        for a in again.pending_approvals():
            again.decide(a["id"], "approve")
        again.run_cycle()
        again.commit()
        self.assertEqual(len(again.ingestion.inbox), 0)
        third, _ = build_live_app(ProviderConfig(), d, FakeProtector(), clock=lambda: NOW)
        self.assertEqual(len(third.ingestion.inbox), 0)                     # not redone a third time
        topic = next(t for t in third.repo.topics.values() if t.name == "Lists")
        ev = [e for e in third.repo.evidence.values() if e.topic_id == topic.id]
        self.assertEqual(len(ev), 3)                                        # exactly once per file
