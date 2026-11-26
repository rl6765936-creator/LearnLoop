"""Simulated transfer: the REAL Local Collector code (synthetic files) -> TransferClient ->
JSON bytes -> the brain's Ingestion API -> Supervisor. No network: the bytes are handed over
in-process by a simulation transport. Only the JSON contract is shared between the projects.

Runs when the Collector project is found next to this one (Desktop\\learnloop-collector) or via
the LEARNLOOP_COLLECTOR environment variable; skipped otherwise.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from brain.app import LearnLoopApp
from brain.model_gateway import FakeModel
from helpers import analysis

BRAIN_ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = Path(os.environ.get("LEARNLOOP_COLLECTOR", BRAIN_ROOT.parent / "learnloop-collector"))
FOUND = (COLLECTOR / "src" / "collector" / "transfer" / "client.py").exists()


@unittest.skipUnless(FOUND, "Local Collector project not found next to the brain project")
class TestSimulatedTransfer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(COLLECTOR / "src"))

    def setUp(self):
        from collector.monitor.monitor import Monitor
        from collector.permissions import folder_access
        from collector.transfer.envelope import source_id
        self.fa = folder_access
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "study"
        (self.root / "python").mkdir(parents=True)
        self.cfg, self.outbox, self.state = base / "settings.json", base / "outbox", base / "tstate.json"
        self.assertTrue(folder_access.grant_folder(str(self.root), self.cfg, home=base / "nohome").ok)
        folder_access.set_transfer_permission(True, self.cfg)
        self.mstate = base / "state.json"
        self.monitor = Monitor(self.cfg, self.mstate, settle_seconds=0, outbox_dir=self.outbox,
                               review_path=base / "review.json", manual_path=base / "manual.json")
        self.model = FakeModel(lambda chunks, known: analysis(
            subject="Python", topics=(("Lists", 0.9),),
            evidence=[{"topic": "Lists", "type": "exposure", "authorship": "unknown", "chunk_id": chunks[0]["chunk_id"]}]))
        self.app = LearnLoopApp(self.model, clock=lambda: "2026-11-24T10:00:00+00:00", engine="plain")
        self.app.set_transfer_permission(source_id(str(self.root)), True)   # same approval, server copy

    def tearDown(self):
        self._tmp.cleanup()

    def transfer(self):
        from collector.transfer.client import TransferClient
        from collector.transfer.transports import CallbackTransport
        sent = []

        def wire(data: bytes) -> dict:          # what would cross the network: bytes only
            sent.append(data)
            return self.app.receive(data)

        report = TransferClient(CallbackTransport(wire), self.cfg, self.outbox, self.state,
                                monitor_state_path=self.mstate).run()
        return report, sent

    def test_contract_copies_are_identical(self):
        mine = (BRAIN_ROOT / "contracts" / "transfer_envelope.schema.json").read_text(encoding="utf-8")
        theirs = (COLLECTOR / "contracts" / "transfer_envelope.schema.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(mine), json.loads(theirs))

    def test_file_to_knowledge_graph(self):
        key = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8t"
        (self.root / "python" / "lists.py").write_text(f'KEY = "{key}"\nnums = [3, 1]\nnums.sort()',
                                                       encoding="utf-8")
        self.monitor.run_once()
        report, sent = self.transfer()
        self.assertEqual((report["sent"], report["acknowledged"]), (1, 1))
        self.assertNotIn(key.encode(), b"".join(sent))                       # secret removed locally
        self.app.run_cycle()
        self.app.decide(self.app.pending_approvals()[0]["id"], "approve")
        self.assertEqual([t.name for t in self.app.repo.topics.values()], ["Lists"])
        chunk_texts = " ".join(c.text for c in self.app.repo.chunks.values())
        self.assertIn("[REDACTED:api_key]", chunk_texts)

    def test_resend_after_lost_acknowledgement_is_harmless(self):
        (self.root / "a.py").write_text("x = 1", encoding="utf-8")
        self.monitor.run_once()
        _, sent = self.transfer()
        again = self.app.receive(sent[0])                                    # same bytes again
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.app.ingestion.inbox), 1)

    def test_server_refuses_when_its_permission_copy_is_revoked(self):
        from collector.transfer.envelope import source_id
        self.app.set_transfer_permission(source_id(str(self.root)), False)
        (self.root / "a.py").write_text("x = 1", encoding="utf-8")
        self.monitor.run_once()
        report, _ = self.transfer()
        self.assertEqual(report["acknowledged"], 0)
        self.assertEqual(self.app.ingestion.inbox, [])

    def test_collector_revoke_means_nothing_is_sent(self):
        (self.root / "a.py").write_text("x = 1", encoding="utf-8")
        self.monitor.run_once()
        self.fa.revoke_folder(self.cfg)
        report, sent = self.transfer()
        self.assertEqual(sent, [])

    def test_deleted_file_stops_being_used_in_the_brain(self):
        path = self.root / "a.py"
        path.write_text("x = 1", encoding="utf-8")
        self.monitor.run_once()
        self.transfer()
        self.app.run_cycle()
        self.app.decide(self.app.pending_approvals()[0]["id"], "approve")
        path.unlink()
        self.monitor.run_once()
        self.transfer()
        self.app.run_cycle()
        self.assertEqual(self.app.repo.active_chunks(), [])
        self.assertTrue(self.app.repo.evidence)                               # history kept


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(FOUND, "Local Collector project not found next to the brain project")
class TestLocalHttpLink(unittest.TestCase):
    """Decision Q8/Q10: the real local link – Collector -> 127.0.0.1 -> brain, with the token;
    and classification answers going back to the Collector (folder/status/period only)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(COLLECTOR / "src"))

    def test_send_over_localhost_and_sync_classification(self):
        import threading
        from http.server import ThreadingHTTPServer
        from brain.api.server import make_handler
        from collector.collections import set_collection
        from collector.monitor.monitor import Monitor
        from collector.permissions import folder_access
        from collector.transfer.brain_link import LocalBrainTransport, sync_classifications
        from collector.transfer.client import TransferClient
        from collector.config import load_config
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        root = base / "study"
        for folder in ("year1", "now"):
            (root / folder).mkdir(parents=True)
            (root / folder / "a.py").write_text(f"# {folder}\nnums = [3, 1]\nnums.sort()", encoding="utf-8")
        cfg = base / "settings.json"
        self.assertTrue(folder_access.grant_folder(str(root), cfg, home=base / "nohome").ok)
        folder_access.set_transfer_permission(True, cfg)
        set_collection("now", "active", "2026", cfg)
        mstate = base / "state.json"
        Monitor(cfg, mstate, settle_seconds=0, outbox_dir=base / "outbox", review_path=base / "r.json",
                manual_path=base / "m.json").run_once()
        model = FakeModel(lambda chunks, known: analysis(subject="Python", topics=(("Lists", 0.9),)))
        app = LearnLoopApp(model, clock=lambda: "2026-11-24T10:00:00+00:00", engine="plain")
        token = "tok-" + "x" * 40
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, ingest_token=token))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = LocalBrainTransport(server.server_address[1], token)
        report = TransferClient(transport, cfg, base / "outbox", base / "t.json", monitor_state_path=mstate).run()
        self.assertEqual(report["acknowledged"], 2, report)
        # she answers the classification question for "year1" in the brain
        app.run_cycle()
        for a in app.pending_approvals():                 # new subject: she approves it first
            app.decide(a["id"], "approve")
        pending = app.pending_classifications()
        self.assertTrue(pending, "the unclassified folder should produce a question")
        items = [i for g in pending for i in g["items"]]
        self.assertEqual([i["collection"] for i in items], ["year1"])     # "now" was set in the Collector
        app.classify(items[0]["id"], "completed", "שנה א'")
        done = sync_classifications(transport, cfg)
        self.assertEqual(done, ["year1"])
        cols = load_config(cfg).config.collections
        self.assertEqual(cols["year1"]["study_status"], "completed")
        self.assertEqual(cols["now"], {"study_status": "active", "period": "2026"})   # untouched
        with self.assertRaises(ValueError):
            LocalBrainTransport(server.server_address[1], token, host="example.com")


@unittest.skipUnless(FOUND, "Local Collector project not found next to the brain project")
class TestSourcesOverLocalLink(unittest.TestCase):
    """Q11/Q12 end to end: Git commits (filtered) and ActivityWatch summaries -> 127.0.0.1 -> brain."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(COLLECTOR / "src"))

    def test_git_and_activity(self):
        import threading
        from datetime import date
        from http.server import ThreadingHTTPServer
        from brain.api.server import make_handler
        from collector.config import save_config, CollectorConfig
        from collector.sources.activity_transfer import send_days
        from collector.sources.git_repos import CommitText
        from collector.sources.git_transfer import send_commits
        from collector.transfer.brain_link import LocalBrainTransport
        tmp = Path(tempfile.mkdtemp())
        cfg = tmp / "c.json"
        save_config(CollectorConfig(git_repos=[{"path": str(tmp / "repo"), "grant_id": "git-g1", "transfer": True},
                                               {"path": str(tmp / "other"), "grant_id": "git-g2"}],
                                    activitywatch={"grant_id": "aw-1", "transfer": True}), cfg)
        model = FakeModel(lambda chunks, known: analysis(subject="Python", topics=(("Lists", 0.9),)))
        app = LearnLoopApp(model, clock=lambda: "2026-11-24T10:00:00+00:00", engine="plain")
        token = "tok-" + "y" * 40
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, ingest_token=token))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        link = LocalBrainTransport(server.server_address[1], token)
        read = []

        def reader(path, seen=None):
            read.append(Path(path).name)
            return [c for c in (CommitText("a" * 40, 1795500000, "sort list", "+nums.sort()", "clean"),
                                CommitText("b" * 40, 1795500100, "x", "[uncertain]", "pending_review"))
                    if c.commit_id not in (seen or set())]
        state = tmp / "git_sent.json"
        r1 = send_commits(link, cfg, state, reader)
        self.assertEqual((r1["sent"], r1["held"]), (1, 1))
        self.assertEqual(read, ["repo"])                       # transfer off for "other": not even read
        self.assertEqual(send_commits(link, cfg, state, reader)["sent"], 0)   # not twice
        self.assertEqual(len(app.ingestion.inbox), 1)
        self.assertEqual(app.ingestion.inbox[0]["source"]["type"], "git")

        class Api:
            def call(self, method, path, body=None):
                if path == "/api/0/buckets/":
                    return {"aw-watcher-window_H": {}, "aw-watcher-afk_H": {}}
                if path == "/api/0/query/":
                    return [[{"duration": 600, "data": {"app": "pycharm64.exe", "title": "secret"}}]]
                return None
        act = send_days(link, cfg, tmp / "aw.json", today=date(2026, 11, 24), api=Api(), days=2)
        self.assertEqual(act["sent"], 2)
        self.assertEqual(app.activity_days["2026-11-23"], [{"app": "pycharm64.exe", "category": None, "minutes": 10}])
        self.assertNotIn("secret", json.dumps(app.activity_days))
