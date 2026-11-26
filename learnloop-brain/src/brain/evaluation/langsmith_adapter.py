"""LangSmith adapter – sends ONLY allow-listed telemetry records (spec §12, §49).

OFF by default. It sends anything only when ALL of these are true:
  - enabled=True was passed explicitly,
  - an API key is configured (LANGSMITH_API_KEY), and
  - the langsmith package is installed.
There is no account / key yet (needs the user) – so in this project it is never on.

Defence in depth: (1) records come from telemetry.MetadataRecorder (no free-text fields);
(2) every record is re-checked against the allow-list here and anything else is dropped;
(3) the real client is created with hide_inputs / hide_outputs / hide_metadata, and the
environment flags LANGSMITH_HIDE_INPUTS / LANGSMITH_HIDE_OUTPUTS are set; (4) run NAMES are
fixed step names (LangSmith does not mask names or tags).
"""

from __future__ import annotations

import os

from brain.evaluation.telemetry import ERROR_CATEGORIES, REQUEST_KINDS, STATUSES, STEPS

FIELDS = {"run_id", "scenario", "step", "seconds", "status", "error_category", "request_kinds",
          "model_attempts"}


def sanitize(record: dict) -> dict | None:
    """Return a safe copy, or None if the record cannot be made safe."""
    if set(record) - FIELDS:
        record = {k: v for k, v in record.items() if k in FIELDS}
    if record.get("step") not in STEPS | {"other"}:
        return None
    if record.get("status") not in STATUSES | {"other"}:
        return None
    if record.get("error_category") not in set(ERROR_CATEGORIES) | {None}:
        return None
    kinds = record.get("request_kinds", [])
    if not isinstance(kinds, list) or not set(kinds) <= REQUEST_KINDS:
        return None
    for key in ("run_id", "scenario"):
        value = str(record.get(key, ""))
        if not value.replace("-", "").replace("_", "").isalnum() or len(value) > 64:
            return None
    if not isinstance(record.get("seconds"), (int, float)) or not isinstance(record.get("model_attempts"), int):
        return None
    return dict(record)


class LangSmithExporter:
    def __init__(self, enabled: bool = False, client=None, project: str = "learnloop-dev"):
        self.enabled = enabled
        self.project = project
        self._client = client          # injected in tests; created lazily otherwise
        self.dropped = 0

    def _active_client(self):
        if not self.enabled:
            return None
        if self._client is not None:
            return self._client
        if not os.environ.get("LANGSMITH_API_KEY"):
            return None
        try:
            from langsmith import Client
        except ImportError:
            return None
        os.environ["LANGSMITH_HIDE_INPUTS"] = "true"
        os.environ["LANGSMITH_HIDE_OUTPUTS"] = "true"
        self._client = Client(hide_inputs=lambda _: {}, hide_outputs=lambda _: {},
                              hide_metadata=lambda _: {})
        return self._client

    def export(self, records: list[dict]) -> int:
        """Returns how many records were sent (0 when off)."""
        client = self._active_client()
        if client is None:
            return 0
        sent = 0
        for record in records:
            safe = sanitize(record)
            if safe is None:
                self.dropped += 1
                continue
            client.create_run(name=f"learnloop.{safe['step']}", run_type="chain", inputs={},
                              outputs={}, extra={"metadata": safe}, project_name=self.project)
            sent += 1
        return sent
