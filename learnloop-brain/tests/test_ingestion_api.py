import json
import unittest

from brain.domain.repository import InMemoryRepository
from brain.ingestion_api import MAX_ENVELOPE_BYTES, IngestionService
from helpers import SOURCE, envelope


class TestIngestionApi(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryRepository()
        self.repo.set_transfer_permission(SOURCE, True)
        self.api = IngestionService(self.repo)

    def test_accepts_valid_bytes(self):
        ack = self.api.receive(json.dumps(envelope(["x"])).encode("utf-8"))
        self.assertTrue(ack["accepted"])
        self.assertEqual(len(self.api.inbox), 1)

    def test_rejects_garbage_oversize_and_contract_violations(self):
        self.assertFalse(self.api.receive(b"\xff\xfe not json")["accepted"])
        self.assertFalse(self.api.receive(b"x" * (MAX_ENVELOPE_BYTES + 1))["accepted"])
        bad = envelope(["x"])
        bad["content_trust"] = "trusted"
        self.assertFalse(self.api.receive(bad)["accepted"])
        self.assertEqual(self.api.inbox, [])

    def test_source_without_permission_is_refused(self):
        self.repo.set_transfer_permission(SOURCE, False)
        self.assertEqual(self.api.receive(envelope(["x"]))["reason"], "source is not approved for transfer")

    def test_resend_is_acknowledged_but_stored_once(self):
        env = envelope(["x"])
        self.api.receive(env)
        ack = self.api.receive(env)
        self.assertEqual((ack["accepted"], ack["duplicate"]), (True, True))
        self.assertEqual(len(self.api.inbox), 1)


if __name__ == "__main__":
    unittest.main()
