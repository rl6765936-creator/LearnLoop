"""B7 – Transfer Client (Collector side). Dry run and simulation only; synthetic files."""

import json
import tempfile
import unittest
from pathlib import Path

from collector.config import CollectorConfig, load_config, save_config
from collector.monitor.monitor import Monitor
from collector.permissions import folder_access
from collector.transfer import envelope as env_mod
from collector.transfer.client import TransferClient
from collector.transfer.transports import CallbackTransport, DryRunTransport, OfflineTransport

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "transfer_envelope.schema.json"


class Receiver:
    """Plays the server: parses bytes, acknowledges, remembers what arrived."""

    def __init__(self, accept=True):
        self.accept, self.got = accept, []

    def __call__(self, data: bytes) -> dict:
        env = json.loads(data.decode("utf-8"))
        self.got.append(env)
        return {"accepted": self.accept}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.base = base
        self.root = base / "study"
        self.root.mkdir()
        self.cfg = base / "settings.json"
        self.outbox = base / "outbox"
        self.state = base / "transfer_state.json"
        self.assertTrue(folder_access.grant_folder(str(self.root), self.cfg, home=base / "nohome").ok)
        self.monitor = Monitor(self.cfg, base / "state.json", settle_seconds=0, outbox_dir=self.outbox,
                               review_path=base / "review.json", manual_path=base / "manual.json")

    def tearDown(self):
        self._tmp.cleanup()

    def collect(self, name="a.txt", text="lesson one"):
        (self.root / name).write_text(text, encoding="utf-8")
        self.monitor.run_once()

    def client(self, transport):
        return TransferClient(transport, self.cfg, self.outbox, self.state,
                              monitor_state_path=self.base / "state.json")


class TestPermission(Base):
    def test_no_transfer_without_the_separate_permission(self):
        self.collect()
        rx = Receiver()
        report = self.client(CallbackTransport(rx)).run()
        self.assertEqual(rx.got, [])
        self.assertIn("not allowed", report["stopped"])

    def test_transfer_permission_lapses_with_revoke_and_new_grant(self):
        folder_access.set_transfer_permission(True, self.cfg)
        self.assertTrue(load_config(self.cfg).config.transfer_allowed())
        folder_access.revoke_folder(self.cfg)
        self.assertFalse(load_config(self.cfg).config.transfer_allowed())
        folder_access.grant_folder(str(self.root), self.cfg, home=self.root.parent / "nohome")
        self.assertFalse(load_config(self.cfg).config.transfer_allowed())   # must be given again

    def test_revoke_in_the_middle_of_a_transfer_stops_the_rest(self):
        for i in range(3):
            self.collect(f"f{i}.txt", f"text {i}")
        folder_access.set_transfer_permission(True, self.cfg)
        rx = Receiver()

        def receive_then_revoke(data):
            ack = rx(data)
            folder_access.revoke_folder(self.cfg)
            return ack

        report = self.client(CallbackTransport(receive_then_revoke)).run()
        self.assertEqual(len(rx.got), 1)
        self.assertIsNotNone(report["stopped"])

    def test_paused_sends_nothing(self):
        self.collect()
        folder_access.set_transfer_permission(True, self.cfg)
        folder_access.set_paused(True, self.cfg)
        rx = Receiver()
        self.client(CallbackTransport(rx)).run()
        self.assertEqual(rx.got, [])


class TestSending(Base):
    def setUp(self):
        super().setUp()
        folder_access.set_transfer_permission(True, self.cfg)

    def test_dry_run_sends_nothing_and_remembers_nothing(self):
        self.collect()
        report = self.client(DryRunTransport()).run()
        self.assertEqual([w["file"] for w in report["would_send"]], ["a.txt"])
        self.assertFalse(self.state.exists())

    def test_envelope_shape_matches_the_shared_contract(self):
        self.collect()
        rx = Receiver()
        self.client(CallbackTransport(rx)).run()
        env = rx.got[0]
        schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(set(env), set(schema["properties"]))
        self.assertEqual(env["content_trust"], "untrusted_user_file")
        self.assertNotIn(str(self.root), json.dumps(env))          # folder path never sent

    def test_acknowledged_content_is_never_sent_again(self):
        self.collect()
        rx = Receiver()
        self.client(CallbackTransport(rx)).run()
        self.client(CallbackTransport(rx)).run()
        self.assertEqual(len(rx.got), 1)

    def test_changed_file_is_sent_as_modified_and_removed_file_as_removal(self):
        self.collect()
        rx = Receiver()
        self.client(CallbackTransport(rx)).run()
        self.collect(text="lesson one – edited")
        self.client(CallbackTransport(rx)).run()
        (self.root / "a.txt").unlink()
        self.monitor.run_once()
        self.client(CallbackTransport(rx)).run()
        self.assertEqual([e["file"]["change"] for e in rx.got], ["new", "modified", "removed"])

    def test_offline_keeps_everything_queued(self):
        self.collect()
        report = self.client(OfflineTransport()).run()
        self.assertIn("offline", report["stopped"])
        rx = Receiver()
        self.client(CallbackTransport(rx)).run()                  # connection is back
        self.assertEqual(len(rx.got), 1)

    def test_not_acknowledged_is_sent_again_next_time(self):
        self.collect()
        self.client(CallbackTransport(Receiver(accept=False))).run()
        rx = Receiver()
        self.client(CallbackTransport(rx)).run()
        self.assertEqual(len(rx.got), 1)

    def test_secret_never_leaves(self):
        key = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8t"
        self.collect("hw.py", f'KEY = "{key}"\nprint(1)')
        rx = Receiver()
        self.client(CallbackTransport(rx)).run()
        blob = json.dumps(rx.got)
        self.assertNotIn(key, blob)
        self.assertIn("[REDACTED:api_key]", blob)


class TestChunking(unittest.TestCase):
    def test_small_segments_are_joined_and_keep_locations(self):
        chunks = env_mod.make_chunks([{"location": "page 1", "text": "a"}, {"location": "page 2", "text": "b"}])
        self.assertEqual([(c["location"], c["text"]) for c in chunks], [("page 1 – page 2", "a\n\nb")])

    def test_very_long_segment_is_split_with_part_numbers(self):
        text = "x" * (env_mod.MAX_CHUNK_CHARS * 2 + 5)
        chunks = env_mod.make_chunks([{"location": "page 7", "text": text}])
        self.assertEqual([c["location"] for c in chunks], ["page 7 (part 1)", "page 7 (part 2)", "page 7 (part 3)"])
        self.assertEqual("".join(c["text"] for c in chunks), text)
        self.assertEqual([c["index"] for c in chunks], [0, 1, 2])

    def test_huge_file_becomes_several_envelopes_with_distinct_keys(self):
        segs = [{"location": f"page {i}", "text": "y" * env_mod.MAX_CHUNK_CHARS}
                for i in range(env_mod.MAX_CHUNKS_PER_ENVELOPE + 3)]
        envs = env_mod.build_envelopes({"source": "big.pdf", "sha256": "a" * 64, "segments": segs},
                                       "folder-x", "g", "new")
        self.assertEqual(len(envs), 2)
        self.assertEqual(len({e["idempotency_key"] for e in envs}), 2)
        self.assertEqual(envs[1]["chunks"][0]["index"], env_mod.MAX_CHUNKS_PER_ENVELOPE)


if __name__ == "__main__":
    unittest.main()
