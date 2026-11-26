"""Repository – where the knowledge graph is stored.

`Repository` is the interface; `InMemoryRepository` is the only implementation in stage
B0–B3 (tests, no database server). PostgreSQL (APPROVED) comes in stage B6 behind the
same interface. The ORM / SQL layer is [OPEN], so nothing here depends on one.

History is APPEND-ONLY: records are added, never changed or removed.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable

from brain.i18n import msg
from brain.domain.models import (ChangeRecord, Chunk, ContentItem, Edge, Evidence, Subject,
                                 Topic, normalize_name)


def content_id(source_id: str, rel_path: str) -> str:
    """History id of a file. A hash, so the file PATH never appears in readable columns."""
    return "content_" + hashlib.sha256(f"{source_id}\x00{rel_path}".encode("utf-8")).hexdigest()[:20]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InMemoryRepository:
    def __init__(self, clock: Callable[[], str] = _utc_now):
        self.clock = clock
        self.subjects: dict[str, Subject] = {}
        self.topics: dict[str, Topic] = {}
        self.edges: set[Edge] = set()
        self.evidence: dict[str, Evidence] = {}
        self.content: dict[tuple[str, str], ContentItem] = {}   # (source_id, rel_path)
        self.chunks: dict[str, Chunk] = {}
        self.transfer_allowed: dict[str, bool] = {}            # source_id -> per_source permission
        self.history: list[ChangeRecord] = []
        self._applied: set[str] = set()                         # idempotency keys
        # Proposed "X is needed before Y" where X is not a topic YET (it may arrive in a later
        # file): (subject_id, normalized name of X, id of Y). Resolved when X appears.
        self.pending_prerequisites: list[tuple[str, str, str]] = []
        # Study contexts (source, collection, subject) -> StudyContext, and the grouped,
        # non-blocking classification questions (decision 2026-11-24). See domain/study.py.
        self.study_contexts: dict[tuple[str, str, str], object] = {}
        self.skills: dict[str, object] = {}                     # spec §13 Skill (partOf a topic)
        self.classification_requests: dict[str, dict] = {}
        self._ids = itertools.count(1)

    # ---------- ids / idempotency ----------
    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids)}"

    def already_applied(self, key: str) -> bool:
        return key in self._applied

    def mark_applied(self, key: str) -> None:
        self._applied.add(key)

    # ---------- permissions (server-side copy, checked again on arrival) ----------
    def set_transfer_permission(self, source_id: str, allowed: bool) -> None:
        self.transfer_allowed[source_id] = allowed

    def may_receive_from(self, source_id: str) -> bool:
        return self.transfer_allowed.get(source_id, False)     # fail closed

    # ---------- history ----------
    def record(self, entity: str, entity_id: str, action: str, before, after, reason: str,
               mode: str, approval_id: str | None = None,
               evidence_ids: list[str] | None = None) -> ChangeRecord:
        rec = ChangeRecord(len(self.history) + 1, self.clock(), entity, entity_id, action,
                           copy.deepcopy(before), copy.deepcopy(after), reason, mode,
                           approval_id, list(evidence_ids or []))
        self.history.append(rec)
        return rec

    def timeline(self, entity_id: str) -> list[ChangeRecord]:
        """How an entity developed over time (spec phase 2 completion criterion)."""
        related = {entity_id} | {e.id for e in self.evidence.values() if e.topic_id == entity_id}
        return [r for r in self.history if r.entity_id in related]

    # ---------- subjects / topics ----------
    def find_subject(self, name: str) -> Subject | None:
        key = normalize_name(name)
        return next((s for s in self.subjects.values() if normalize_name(s.name) == key), None)

    def add_subject(self, name: str, approval_id: str, reason: str) -> Subject:
        subject = Subject(self.new_id("subj"), name.strip(), approval_id)
        self.subjects[subject.id] = subject
        self.record("subject", subject.id, "created", None, asdict(subject), reason,
                    "approved", approval_id)
        return subject

    def find_topic(self, subject_id: str, name: str) -> Topic | None:
        key = normalize_name(name)
        return next((t for t in self.topics.values()
                     if t.subject_id == subject_id and normalize_name(t.name) == key), None)

    def add_topic(self, subject_id: str, name: str, reason: str, mode: str,
                  approval_id: str | None = None) -> Topic:
        topic = Topic(self.new_id("topic"), subject_id, name.strip())
        self.topics[topic.id] = topic
        self.record("topic", topic.id, "created", None, asdict(topic), reason, mode, approval_id)
        return topic

    # ---------- skills ----------
    def find_skill(self, topic_id: str, name: str):
        key = name.strip().casefold()
        return next((k for k in self.skills.values() if k.topic_id == topic_id and k.name.casefold() == key), None)

    def add_skill(self, topic_id: str, name: str, reason, mode: str):
        from brain.domain.models import Skill
        skill = Skill(self.new_id("skill"), topic_id, name.strip())
        self.skills[skill.id] = skill
        self.record("skill", skill.id, "created", None, asdict(skill), reason, mode)
        self.add_edge(Edge(skill.id, topic_id, "partOf"), reason, mode)
        return skill

    # ---------- edges ----------
    def would_create_cycle(self, src: str, dst: str) -> bool:
        """Adding prerequisite src->dst creates a cycle if dst already (indirectly) precedes src."""
        if src == dst:
            return True
        stack, seen = [dst], set()
        while stack:
            node = stack.pop()
            if node == src:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack += [e.dst for e in self.edges if e.kind == "prerequisite" and e.src == node]
        return False

    def add_edge(self, edge: Edge, reason: str, mode: str, approval_id: str | None = None) -> bool:
        if edge in self.edges:
            return False
        self.edges.add(edge)
        self.record("edge", f"{edge.src}->{edge.dst}:{edge.kind}", "created", None, asdict(edge),
                    reason, mode, approval_id)
        return True

    # ---------- evidence ----------
    def add_evidence(self, evidence: Evidence, reason: str, mode: str,
                     approval_id: str | None = None) -> None:
        self.evidence[evidence.id] = evidence
        self.record("evidence", evidence.id, "created", None, asdict(evidence), reason, mode,
                    approval_id, [evidence.id])

    # ---------- content ----------
    def store_content(self, item: ContentItem, chunks: list[Chunk]) -> None:
        key = (item.source_id, item.rel_path)
        before = asdict(self.content[key]) if key in self.content else None
        self.content[key] = item
        for c in chunks:
            self.chunks[c.chunk_id] = c
        self.record("content", content_id(item.source_id, item.rel_path),
                    "updated" if before else "created", before, asdict(item),
                    msg("h.content_received"), "autonomous")

    def mark_source_removed(self, source_id: str, rel_path: str) -> bool:
        """Spec §4: stop using the information; KEEP evidence, conclusions and history."""
        item = self.content.get((source_id, rel_path))
        if item is None or item.status == "source_removed":
            return False
        before = asdict(item)
        item.status = "source_removed"
        for c in self.chunks.values():
            if c.content_sha == item.sha256:
                c.status = "source_removed"
        self.record("content", content_id(source_id, rel_path), "source_removed", before, asdict(item),
                    msg("h.content_removed"), "autonomous")
        return True

    def active_chunks(self) -> list[Chunk]:
        return [c for c in self.chunks.values() if c.status == "active"]
