"""Local link to the LearnLoop brain on THIS computer (decisions 2026-11-24, Q8 + Q10).

- LocalBrainTransport sends envelopes ONLY to 127.0.0.1 (the brain's local server). Any other
  address is refused in code. Nothing leaves the computer through this transport.
- It carries the brain's local token (local_data/ingest.token of the brain). The token is read
  from a file path the user configures; it is never printed.
- sync_classifications(): the brain sends back the classification ANSWERS she gave there
  (folder, status, period) – no study material, and no new permission for the Collector.
  A folder is updated only if the Collector does not know it yet, and only when every answer
  for that folder agrees on the status. A folder she classified in the Collector is never
  overwritten.
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

from collector.transfer.transports import TransportUnavailable

LOCAL_HOST = "127.0.0.1"


class LocalBrainTransport:
    name = "local-brain"
    sends_data = True

    def __init__(self, port: int, token: str, host: str = LOCAL_HOST, timeout_s: float = 30.0):
        if host != LOCAL_HOST:
            raise ValueError("the brain link is local only (127.0.0.1)")
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("port must be a number between 1024 and 65535")
        if not token or len(token) < 20:
            raise ValueError("missing local token")
        self.port, self._token, self.timeout_s = port, token, timeout_s

    @classmethod
    def from_token_file(cls, port: int, token_file: str | Path) -> "LocalBrainTransport":
        return cls(port, Path(token_file).read_text(encoding="ascii").strip())

    def _post(self, path: str, data: bytes) -> dict:
        conn = http.client.HTTPConnection(LOCAL_HOST, self.port, timeout=self.timeout_s)
        try:
            conn.request("POST", path, data, {"Content-Type": "application/json", "X-LearnLoop": "1",
                                              "X-LearnLoop-Token": self._token,
                                              "Host": f"{LOCAL_HOST}:{self.port}"})
            r = conn.getresponse()
            body = r.read()
        except (ConnectionError, OSError) as e:
            raise TransportUnavailable("the brain is not running") from e
        finally:
            conn.close()
        if r.status == 403:
            raise PermissionError("the brain refused the local token")
        if r.status >= 500:
            raise TransportUnavailable(f"brain error {r.status}")
        return json.loads(body or b"{}")

    def send(self, data: bytes) -> dict:
        return self._post("/api/ingest", data)

    def send_activity(self, summary: dict) -> dict:
        return self._post("/api/ingest/activity", json.dumps(summary, ensure_ascii=False).encode("utf-8"))

    def revoke(self, source_id: str) -> dict:
        return self._post("/api/ingest/revoke", json.dumps({"source_id": source_id}).encode())

    def decided_classifications(self) -> list[dict]:
        return self._post("/api/ingest/classifications", b"{}").get("decided", [])


def plan_sync(rows: list[dict], source: str, known: dict) -> dict[str, dict]:
    """Pure decision: which folders to set. rows = the brain's answers."""
    by_folder: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("source_id") == source and r.get("collection") and r["collection"] not in known:
            by_folder.setdefault(r["collection"], []).append(r)
    plan = {}
    for folder, answers in by_folder.items():
        statuses = {a["study_status"] for a in answers}
        if len(statuses) != 1 or statuses == {"unknown"}:
            continue                           # mixed answers: leave it to her (no guessing)
        periods = {a.get("period") for a in answers}
        plan[folder] = {"study_status": statuses.pop(), "period": periods.pop() if len(periods) == 1 else None}
    return plan


def sync_classifications(transport, config_path=None) -> list[str]:
    from collector.collections import set_collection
    from collector.config import DEFAULT_CONFIG_PATH, load_config
    from collector.transfer.envelope import source_id
    path = config_path or DEFAULT_CONFIG_PATH
    config = load_config(path).config
    if config.approved_folder is None:
        return []
    known = config.collections if config.collections_folder == config.approved_folder else {}
    plan = plan_sync(transport.decided_classifications(), source_id(config.approved_folder), known)
    done = []
    for folder, c in sorted(plan.items()):
        ok, _msg = set_collection(folder, c["study_status"], c["period"], path)
        if ok:
            done.append(folder)
    return done
