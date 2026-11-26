"""Study Status, Assessment Status and study contexts (user decision 2026-11-24, APPROVED).

Three dimensions, kept apart:
  Study Status      completed | active | not_studied | unknown   – EXPLICIT, set by the user
                    (in the Collector's folder classification, or by answering a grouped
                    classification question here). Never inferred from names or content.
  Assessment Status assessed | not_assessed                       – derived from evidence:
                    assessed = at least one piece of evidence that shows HER knowledge
                    (not exposure to material, not AI-generated work).
  Mastery Status    new | learning | understood | reinforce       – the approved states,
                    computed by the approved formula. Not changed here.

A subject can have several study contexts (one per collection/period), so its study status
is a LIST, never one global value. Every piece of evidence keeps its context id.

Classification questions:
  - one per study context (source + collection + subject) – grouped, never per file;
  - NON-blocking: material is processed as "unknown" meanwhile;
  - asked once: the answer is stored and the same question is not asked again;
  - an explicit Collector classification on the envelope settles it without a question.
"""

from __future__ import annotations

from dataclasses import asdict

from brain.domain.models import STUDY_STATUSES, StudyContext
from brain.i18n import msg

DECIDABLE = ("completed", "active", "not_studied")


def context_key(source_id: str, collection: str, subject_id: str) -> tuple[str, str, str]:
    return (source_id, collection, subject_id)


def envelope_context(envelope: dict) -> dict:
    """The Collector's explicit classification (absent in older envelopes = unknown)."""
    c = envelope.get("context") or {}
    return {"collection": c.get("collection", ""), "study_status": c.get("study_status", "unknown"),
            "period": c.get("period"), "modified_at": c.get("modified_at"), "queue": c.get("queue")}


def resolve_context(repo, envelope: dict, subject_id: str, subject_name: str, on_change=None) -> StudyContext:
    """Find or create the study context of this material; apply an explicit classification;
    open ONE grouped question if it is still unknown and was never asked."""
    env = envelope_context(envelope)
    source = envelope["source"]["source_id"]
    key = context_key(source, env["collection"], subject_id)
    ctx = repo.study_contexts.get(key)
    if ctx is None:
        ctx = StudyContext(repo.new_id("ctx"), source, env["collection"], subject_id)
        repo.study_contexts[key] = ctx
        repo.record("study_context", ctx.id, "created", None, asdict(ctx), msg("h.context_created"), "autonomous")
    if env["study_status"] in DECIDABLE and (env["study_status"], env["period"]) != (ctx.study_status, ctx.period):
        _set(repo, ctx, env["study_status"], env["period"], "collector_setting", msg("h.context_from_setting"), "user")
        if on_change:
            on_change(ctx)
    if ctx.study_status == "unknown":
        _ask_once(repo, ctx, subject_name)
    return ctx


def _set(repo, ctx: StudyContext, status: str, period: str | None, by: str, reason, mode: str,
         approval_id: str | None = None) -> None:
    before = asdict(ctx)
    ctx.study_status, ctx.period, ctx.decided_by = status, period, by
    repo.record("study_context", ctx.id, "updated", before, asdict(ctx), reason, mode, approval_id)
    # a question that is no longer needed is closed (recorded, not deleted)
    for q in repo.classification_requests.values():
        if q["context_id"] == ctx.id and q["status"] == "pending":
            q["status"] = "settled_by_setting" if by == "collector_setting" else "decided"


def _ask_once(repo, ctx: StudyContext, subject_name: str) -> None:
    if any(q["context_id"] == ctx.id for q in repo.classification_requests.values()):
        return                                                    # asked before – not again
    qid = repo.new_id("cls")
    repo.classification_requests[qid] = {"id": qid, "context_id": ctx.id, "status": "pending",
                                         "subject_id": ctx.subject_id, "subject": subject_name,
                                         "collection": ctx.collection, "files": 0}
    repo.record("classification_request", qid, "created", None, {"context_id": ctx.id},
                msg("h.classification_asked"), "autonomous")


def count_file(repo, ctx: StudyContext) -> None:
    for q in repo.classification_requests.values():
        if q["context_id"] == ctx.id and q["status"] == "pending":
            q["files"] += 1


def topics_of_context(repo, ctx_id: str) -> list:
    ids = {e.topic_id for e in repo.evidence.values() if e.context_id == ctx_id}
    return [t for t in repo.topics.values() if t.id in ids]


def decide(repo, request_id: str, status: str, period: str | None = None, on_change=None) -> dict:
    q = repo.classification_requests.get(request_id)
    if q is None or q["status"] != "pending":
        raise ValueError("unknown or already answered classification question")
    if status not in DECIDABLE:
        raise ValueError("study status must be completed, active or not_studied")
    if period is not None and (not isinstance(period, str) or len(period) > 80):
        raise ValueError("period: short text")
    ctx = next(c for c in repo.study_contexts.values() if c.id == q["context_id"])
    _set(repo, ctx, status, period, "user_decision", msg("h.context_user"), "user", request_id)
    q["status"], q["answer"] = "decided", {"study_status": status, "period": period}
    if on_change:
        on_change(ctx)
    return {"context_id": ctx.id, "study_status": status}


def pending_questions(repo) -> list[dict]:
    """Grouped by subject: one entry per subject, listing its unclassified collections."""
    groups: dict[str, dict] = {}
    for q in repo.classification_requests.values():
        if q["status"] != "pending":
            continue
        g = groups.setdefault(q["subject_id"], {"subject_id": q["subject_id"], "subject": q["subject"], "items": []})
        g["items"].append({"id": q["id"], "collection": q["collection"], "files": q["files"]})
    return list(groups.values())


# --------------------------------------------------------------------------- derived views
def _ctx_by_id(repo) -> dict:
    return {c.id: c for c in repo.study_contexts.values()}


def assessment_status(repo, topic_id: str) -> str:
    """assessed = there is evidence of HER knowledge (not exposure, not AI work)."""
    for e in repo.evidence.values():
        if e.topic_id == topic_id and e.type != "exposure" and e.counts_as_user_ability \
                and e.authorship != "ai":
            return "assessed"
    return "not_assessed"


def topic_study(repo, topic_id: str) -> dict:
    """Study contexts in which this topic appears, and a summary that never hides a
    difference between periods (several statuses = 'mixed', with the list)."""
    by_id = _ctx_by_id(repo)
    contexts = {}
    for e in repo.evidence.values():
        if e.topic_id == topic_id and e.context_id in by_id:
            c = by_id[e.context_id]
            contexts[c.id] = {"collection": c.collection, "period": c.period, "study_status": c.study_status}
    statuses = sorted({c["study_status"] for c in contexts.values()})
    summary = statuses[0] if len(statuses) == 1 else ("mixed" if statuses else "unknown")
    return {"summary": summary, "contexts": list(contexts.values())}


def subject_periods(repo, subject_id: str) -> list[dict]:
    return [{"context_id": c.id, "collection": c.collection, "period": c.period, "study_status": c.study_status,
             "decided_by": c.decided_by}
            for c in repo.study_contexts.values() if c.subject_id == subject_id]


def topics_in_status(repo, subject_id: str, status: str) -> list:
    """Topics that appear in a context with this study status."""
    ids = {c.id for c in repo.study_contexts.values() if c.subject_id == subject_id and c.study_status == status}
    topic_ids = {e.topic_id for e in repo.evidence.values() if e.context_id in ids}
    return [t for t in repo.topics.values() if t.id in topic_ids]


assert set(DECIDABLE) | {"unknown"} == set(STUDY_STATUSES)
