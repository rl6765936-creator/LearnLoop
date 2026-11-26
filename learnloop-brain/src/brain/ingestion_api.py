"""Ingestion API (server side of the transfer – brain-design §3, step 3).

Framework-independent: `receive()` takes the raw bytes of ONE envelope (as they would arrive
over HTTPS) and returns an acknowledgement. An HTTP layer only has to call it; the backend
framework is still [OPEN].

Checks, in order (fail closed): size limit -> valid JSON -> contract -> per_source transfer
permission (second, server-side check) -> duplicate. Accepted envelopes wait in the inbox
until the next processing cycle (spec §35: routine cycle, not on every change).
Nothing here makes the Collector send anything: real transfer is not enabled (Q-D).
"""

from __future__ import annotations

import json

from brain import contracts

MAX_ENVELOPE_BYTES = 5 * 1024 * 1024     # [ASSUMPTION] large files arrive as several chunked envelopes


class IngestionService:
    def __init__(self, repo):
        self.repo = repo
        self.inbox: list[dict] = []

    def receive(self, raw: bytes | str | dict) -> dict:
        if isinstance(raw, dict):
            envelope = raw
        else:
            data = raw.encode("utf-8") if isinstance(raw, str) else raw
            if len(data) > MAX_ENVELOPE_BYTES:
                return self._nack("too large")
            try:
                envelope = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return self._nack("not valid JSON")
        try:
            contracts.validate(contracts.ENVELOPE, envelope)
        except contracts.ContractError as e:
            return self._nack(f"contract: {e}")
        key = envelope["idempotency_key"]
        if not self.repo.may_receive_from(envelope["source"]["source_id"]):
            return self._nack("source is not approved for transfer", key)
        if self.repo.already_applied(key) or any(e["idempotency_key"] == key for e in self.inbox):
            # Already have it: acknowledge again, so a resend after a lost ack is harmless.
            return {"accepted": True, "duplicate": True, "idempotency_key": key}
        self.inbox.append(envelope)
        return {"accepted": True, "duplicate": False, "idempotency_key": key}

    def take(self) -> list[dict]:
        batch, self.inbox = self.inbox, []
        return batch

    def put_back(self, envelopes: list[dict]) -> None:
        """Failed items return to the inbox for the next cycle (spec §35)."""
        self.inbox = envelopes + self.inbox

    @staticmethod
    def _nack(reason: str, key: str | None = None) -> dict:
        return {"accepted": False, "reason": reason, "idempotency_key": key}
