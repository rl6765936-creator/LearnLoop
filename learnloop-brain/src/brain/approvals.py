"""Approval Center – keeps approval requests until the USER decides (spec §11, §16).

Rules ([RECOMMENDATION] for the GAP in spec §7.3):
- requests never expire and are never resolved automatically;
- each batch waits in its own workflow thread, so a pending request does not block others;
- every decision is written to the history (who decided, what, when).
"""

from __future__ import annotations

from brain import contracts
from brain.i18n import msg
from brain.domain.repository import InMemoryRepository


class ApprovalCenter:
    def __init__(self, repo: InMemoryRepository):
        self.repo = repo
        self._requests: dict[str, dict] = {}        # request id -> request (+ thread_id)
        self._decisions: dict[str, dict] = {}

    def submit(self, thread_id: str, requests: list[dict]) -> None:
        for r in requests:
            if r["id"] not in self._requests:       # same request again (resume) -> once
                self._requests[r["id"]] = dict(r, thread_id=thread_id)

    def pending(self) -> list[dict]:
        return [r for rid, r in self._requests.items() if rid not in self._decisions]

    def decide(self, request_id: str, action: str, correction: dict | None = None) -> dict:
        if request_id not in self._requests:
            raise KeyError("unknown approval request")
        if request_id in self._decisions:
            raise ValueError("this request was already decided")
        decision = {"request_id": request_id, "action": action}
        if correction:
            decision["correction"] = correction
        contracts.validate(contracts.DECISION, decision)
        self._decisions[request_id] = decision
        r = self._requests[request_id]
        self.repo.record("approval", request_id, "decided", {"kind": r["kind"], "summary": r["summary"]},
                         decision, msg("h.user_decision"), "user", request_id)
        return decision

    def twins(self, request_id: str) -> list[str]:
        """Other PENDING requests asking the very same question (same kind, same refs).
        Only new_subject is grouped: "is X a new subject?" is one question for the user,
        however many files raised it. [RECOMMENDATION – less burden, spec §1]"""
        r = self._requests[request_id]
        if r["kind"] != "new_subject":
            return []
        return [rid for rid, o in self._requests.items()
                if rid != request_id and rid not in self._decisions
                and o["kind"] == r["kind"] and sorted(o["refs"]) == sorted(r["refs"])]

    def pending_grouped(self) -> list[dict]:
        """Pending requests with identical questions shown once (with a count)."""
        out, seen = [], set()
        for r in self.pending():
            if r["id"] in seen:
                continue
            twins = self.twins(r["id"])
            seen.update(twins)
            out.append(dict(r, count=1 + len(twins)))
        return out

    def thread_of(self, request_id: str) -> str:
        return self._requests[request_id]["thread_id"]

    def ready(self, thread_id: str) -> list[dict] | None:
        """All decisions of a thread once EVERY request in it is decided, else None."""
        ids = [rid for rid, r in self._requests.items() if r["thread_id"] == thread_id]
        if not ids or any(rid not in self._decisions for rid in ids):
            return None
        return [self._decisions[rid] for rid in ids]
