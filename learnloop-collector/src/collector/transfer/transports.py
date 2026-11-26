"""Transports – HOW envelopes leave the Collector. There is deliberately NO network transport.

- DryRunTransport: sends nothing; records what WOULD be sent (for the user to check).
- CallbackTransport: hands the JSON BYTES to a function (simulation / tests, e.g. the brain's
  Ingestion API in another process or test). Only bytes cross – no shared Python objects.
- OfflineTransport: always fails like a missing connection (tests of the local queue).
"""

from __future__ import annotations

import json


class TransportUnavailable(ConnectionError):
    """No connection: keep everything queued locally and try in a later cycle (spec §37)."""


class DryRunTransport:
    name = "dry-run"
    sends_data = False

    def __init__(self):
        self.would_send: list[dict] = []

    def send(self, data: bytes) -> dict:
        env = json.loads(data.decode("utf-8"))
        self.would_send.append({"file": env["file"]["rel_path"], "change": env["file"]["change"],
                                "chunks": len(env["chunks"]), "bytes": len(data),
                                "redactions": len(env["filter"]["redactions"]),
                                "warnings": env["extract"]["warnings"]})
        return {"accepted": False, "dry_run": True}


class CallbackTransport:
    name = "simulation"
    sends_data = True

    def __init__(self, receive):
        self.receive = receive

    def send(self, data: bytes) -> dict:
        return self.receive(data)


class OfflineTransport:
    name = "offline"
    sends_data = True

    def send(self, data: bytes) -> dict:
        raise TransportUnavailable("no connection")
