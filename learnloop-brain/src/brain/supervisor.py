"""Supervisor – the central manager of the brain (spec §8, APPROVED role).

It is ordinary code, not an AI agent ([RECOMMENDATION]: fewer moving parts, spec §8
"do not create agents to look sophisticated"). Its jobs:
  - route each event to the right workflow (today: study content; other event types
    belong to later stages and are answered "unsupported");
  - run every batch in its OWN LangGraph thread, so a batch waiting for the user never
    blocks the others;
  - hand approval requests to the Approval Center, and resume the paused thread as soon
    as the user has decided ALL requests of that thread;
  - record what it did, and give a transparency summary per processing cycle (spec §17).
Failed batches are left unprocessed and can be sent again in the next cycle (spec §35).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from brain.approvals import ApprovalCenter
from brain.i18n import t
from brain.ingestion.engines import LangGraphEngine, PlainEngine
from brain.ingestion.steps import Deps

CONTENT = "content"          # an envelope from the Collector
SUPPORTED_EVENTS = (CONTENT,)


def thread_for(envelope: dict) -> str:
    """Same content -> same thread (resend while waiting does not start a second run)."""
    key = str(envelope.get("idempotency_key", ""))
    return "ingest-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


@dataclass
class CycleSummary:
    applied: list[str] = field(default_factory=list)        # transparency text per batch
    waiting: list[str] = field(default_factory=list)        # thread ids waiting for the user
    failed: list[str] = field(default_factory=list)         # file paths to retry next cycle
    rejected: int = 0
    duplicates: int = 0
    unsupported: int = 0

    def text(self, lang: str | None = None) -> str:
        parts = list(self.applied)
        if self.waiting:
            parts.append(t("cycle.waiting", lang, n=len(self.waiting)))
        if self.failed:
            parts.append(t("cycle.failed", lang, n=len(self.failed)))
        return " ".join(parts) or t("cycle.nothing", lang)


class Supervisor:
    def __init__(self, deps: Deps, checkpointer=None, center: ApprovalCenter | None = None,
                 engine=None):
        """engine: LangGraphEngine (default, APPROVED technology) or PlainEngine."""
        self.deps = deps
        self.engine = engine or LangGraphEngine(deps, checkpointer)
        self.center = center or ApprovalCenter(deps.repo)
        self.thread_status: dict[str, str] = {}
        self.actions: list[dict] = []            # what the Supervisor did (metadata only)

    # ------------------------------------------------------------------ events
    def handle(self, event: dict) -> dict:
        kind = event.get("type")
        if kind not in SUPPORTED_EVENTS:
            self._log(kind, None, "unsupported")
            return {"status": "unsupported"}
        envelope = event.get("envelope") or {}
        thread = thread_for(envelope)
        if self.thread_status.get(thread) == "waiting_for_user":
            return {"status": "waiting_for_user", "thread": thread}   # already paused – no rerun
        return self._after_run(thread, *self.engine.start(envelope, thread))

    def run_cycle(self, events: list[dict]) -> CycleSummary:
        """One processing cycle (every 2 hours or on an early trigger – spec §35)."""
        summary = CycleSummary()
        for event in events:
            result = self.handle(event)
            self._add_to_summary(summary, event, result)
        return summary

    # ------------------------------------------------------------------ user decisions
    def decide(self, request_id: str, action: str, correction: dict | None = None) -> dict | None:
        """Record the user's decision; resume the thread once all its requests are decided.
        Returns the workflow result if it resumed, else None (still waiting for others)."""
        twins = self.center.twins(request_id)
        self.center.decide(request_id, action, correction)
        result = self._resume_if_ready(request_id)
        for twin in twins:                     # the same question from other files: same answer
            self.center.decide(twin, action, correction)
            self._resume_if_ready(twin)
        return result

    def _resume_if_ready(self, request_id: str) -> dict | None:
        thread = self.center.thread_of(request_id)
        decisions = self.center.ready(thread)
        if decisions is None:
            return None
        return self._after_run(thread, *self.engine.resume(thread, decisions))

    # ------------------------------------------------------------------ internals
    def _after_run(self, thread: str, result: dict, requests: list[dict]) -> dict:
        if requests:
            self.center.submit(thread, requests)
            status = "waiting_for_user"
        else:
            status = result.get("status") or "unknown"
        self.thread_status[thread] = status
        self._log(CONTENT, thread, status)
        return dict(result, status=status, thread=thread)

    def _add_to_summary(self, summary: CycleSummary, event: dict, result: dict) -> None:
        status = result.get("status")
        if status == "applied" and result.get("transparency"):
            summary.applied.append(result["transparency"])
        elif status == "waiting_for_user":
            summary.waiting.append(result["thread"])
        elif status == "failed":
            summary.failed.append(event["envelope"]["file"]["rel_path"])
        elif status == "rejected":
            summary.rejected += 1
        elif status == "duplicate":
            summary.duplicates += 1
        elif status == "unsupported":
            summary.unsupported += 1

    def _log(self, event_type, thread, status) -> None:
        self.actions.append({"event": event_type if event_type in SUPPORTED_EVENTS else "other",
                             "thread": thread, "status": status})
