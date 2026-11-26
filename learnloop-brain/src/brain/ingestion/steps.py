"""The steps of the ingestion workflow, as plain functions.

Each step takes the workflow state (a dict) and returns the keys it changes. They do not
depend on LangGraph, so they are tested directly; graph.py only connects them.

Rules that come from how LangGraph resumes after a pause (the paused step runs again from
its start), see brain-design §6.2:
- steps BEFORE the human decision never write to the knowledge graph;
- ALL writes happen in apply_changes, and every write is protected by an idempotency key,
  so running it twice changes nothing the second time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from brain import contracts, policy
from brain.settings import PolicySettings
from brain.i18n import msg, render, t
from brain.domain.mastery import MasteryModel, PlaceholderMasteryModel, reassess
from brain.domain.models import Chunk, ContentItem, Edge, Evidence, normalize_name
from brain.domain.repository import InMemoryRepository
from brain.domain.study import count_file, envelope_context, resolve_context, topics_of_context
from brain.model_gateway import ModelGateway

MAX_MODEL_ATTEMPTS = 3   # [ASSUMPTION] spec §60 requires retries; the number is not defined


@dataclass
class Deps:
    repo: InMemoryRepository
    model: ModelGateway
    mastery: MasteryModel = field(default_factory=PlaceholderMasteryModel)
    settings: object | None = None          # brain.settings.Settings; None = module defaults


def _low(deps: "Deps") -> float:
    return deps.settings.policy.low_confidence if deps.settings else policy.LOW_CONFIDENCE


# --------------------------------------------------------------------------- 1. validate
def validate(state: dict, deps: Deps) -> dict:
    envelope = state["envelope"]
    try:
        contracts.validate(contracts.ENVELOPE, envelope)
    except contracts.ContractError as e:
        return {"status": "rejected", "error": str(e)}
    if not deps.repo.may_receive_from(envelope["source"]["source_id"]):
        # Second, server-side permission check (brain-design §8.1). Not processed, not stored.
        return {"status": "rejected", "error": "source is not approved for transfer"}
    if deps.repo.already_applied(envelope["idempotency_key"]):
        return {"status": "duplicate"}
    if envelope["file"]["change"] == "removed":
        return {"status": "removal"}
    return {"status": "received"}


# --------------------------------------------------------------------------- 2. analyze
def analyze(state: dict, deps: Deps) -> dict:
    envelope = state["envelope"]
    chunks = [{"chunk_id": c["chunk_id"], "location": c["location"], "text": c["text"],
               "trust": envelope["content_trust"]} for c in envelope["chunks"]]
    known = [{"id": s.id, "name": s.name} for s in deps.repo.subjects.values()]
    limit = deps.settings.policy.analysis_window_chars if deps.settings else PolicySettings().analysis_window_chars
    windows = split_windows(chunks, limit)
    answers, attempts, context = [], 0, None
    for window in windows:
        answer, used, last_error = _analyze_window(deps, window, known, context)
        attempts += used
        if answer is None:
            # Spec §35 (APPROVED): failed processing is NOT marked as processed.
            return {"status": "failed", "error": last_error, "attempts": attempts}
        answers.append(answer)
        context = file_context(answers)
    merged = answers[0] if len(answers) == 1 else merge_analyses(answers)
    try:
        contracts.validate(contracts.ANALYSIS, merged)
    except contracts.ContractError as e:
        return {"status": "failed", "error": f"merged analysis rejected: {e}", "attempts": attempts}
    return {"analysis": merged, "attempts": attempts, "parts": len(windows)}


def _analyze_window(deps: Deps, chunks: list[dict], known: list[dict], context: dict | None):
    last_error = ""
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        try:
            answer = deps.model.analyze(chunks, known, context) if context else deps.model.analyze(chunks, known)
            contracts.validate(contracts.ANALYSIS, answer)
            return answer, attempt, ""
        except contracts.ContractError as e:
            last_error = f"model output rejected: {e}"
        except Exception as e:  # provider failure must not crash or lose content
            last_error = f"model call failed ({type(e).__name__})"
    return None, MAX_MODEL_ATTEMPTS, last_error


def split_windows(chunks: list[dict], limit: int) -> list[list[dict]]:
    """Consecutive parts of at most `limit` characters; chunk order and locations are kept.
    A single chunk larger than the limit stays whole (the Collector already caps chunk size)."""
    windows, current, size = [], [], 0
    for c in chunks:
        n = len(c["text"])
        if current and size + n > limit:
            windows.append(current)
            current, size = [], 0
        current.append(c)
        size += n
    if current or not windows:
        windows.append(current)
    return windows


def file_context(answers: list[dict]) -> dict:
    """What earlier parts of the same file contained – names only, no text."""
    last = answers[0]["subject"]
    topics = []
    for a in answers:
        for t in a["topics"]:
            if normalize_name(t["name"]) not in {normalize_name(x) for x in topics}:
                topics.append(t["name"])
    return {"subject": last["name"], "topics": topics[:50]}


def merge_analyses(answers: list[dict]) -> dict:
    """One analysis for one file analysed in several parts: subject = the most frequent (first
    on a tie); topics merged by name (chunk ids and prerequisites joined, highest confidence);
    all evidence kept. The result is validated against the contract again."""
    names = [normalize_name(a["subject"]["name"]) for a in answers]
    best = max(dict.fromkeys(names), key=names.count)
    subject = dict(next(a["subject"] for a in answers if normalize_name(a["subject"]["name"]) == best))
    subject["confidence"] = min(a["subject"]["confidence"] for a in answers
                                if normalize_name(a["subject"]["name"]) == best)
    topics: dict[str, dict] = {}
    for a in answers:
        for t in a["topics"]:
            key = normalize_name(t["name"])
            if key not in topics:
                topics[key] = {k: (list(v) if isinstance(v, list) else v) for k, v in t.items()}
                continue
            m = topics[key]
            m["confidence"] = max(m["confidence"], t["confidence"])
            m["chunk_ids"] += [c for c in t["chunk_ids"] if c not in m["chunk_ids"]]
            pre = m.setdefault("prerequisites", [])
            pre += [p for p in t.get("prerequisites", []) if p not in pre]
            if not m.get("matched_topic_id") and t.get("matched_topic_id"):
                m["matched_topic_id"] = t["matched_topic_id"]
            for field_name in ("related", "skills"):
                extra = m.setdefault(field_name, [])
                extra += [x for x in t.get(field_name, []) if x not in extra]
    return {"subject": subject, "topics": list(topics.values()),
            "evidence": [e for a in answers for e in a["evidence"]]}


# --------------------------------------------------------------------------- 3. plan
def plan(state: dict, deps: Deps) -> dict:
    """Turn the model's proposal into a concrete, checked plan. Still no writes."""
    repo, analysis = deps.repo, state["analysis"]
    chunk_ids = {c["chunk_id"] for c in state["envelope"]["chunks"]}

    proposed = analysis["subject"]
    existing = None
    if proposed.get("matched_subject_id") in repo.subjects:
        existing = repo.subjects[proposed["matched_subject_id"]]
    existing = existing or repo.find_subject(proposed["name"])
    subject = {"name": existing.name if existing else proposed["name"],
               "existing_id": existing.id if existing else None,
               "confidence": proposed["confidence"]}

    topics, dropped = [], []
    seen = set()
    for t in analysis["topics"]:
        key = normalize_name(t["name"])
        if key in seen:
            continue
        seen.add(key)
        refs = [c for c in t["chunk_ids"] if c in chunk_ids]
        if not refs:                     # points to text that was not sent -> not trusted
            dropped.append(t["name"])
            continue
        entry = {"name": t["name"].strip(), "existing_id": None, "confidence": t["confidence"],
                 "chunk_ids": refs, "prerequisites": list(t.get("prerequisites", [])),
                 "related": list(t.get("related", [])), "skills": list(t.get("skills", []))}
        matched = repo.topics.get(t.get("matched_topic_id") or "")
        if matched is not None and existing is not None and matched.subject_id != existing.id:
            entry["conflict"] = (f"'{t['name']}' was matched to a topic of another subject")
        elif existing is not None:
            found = matched or repo.find_topic(existing.id, t["name"])
            entry["existing_id"] = found.id if found else None
        topics.append(entry)

    topic_keys = {normalize_name(t["name"]) for t in topics}
    evidence = [e for e in analysis["evidence"]
                if e["chunk_id"] in chunk_ids and normalize_name(e["topic"]) in topic_keys]
    return {"plan": {"subject": subject, "topics": topics, "evidence": evidence,
                     "dropped_topics": dropped}}


# --------------------------------------------------------------------------- 4. policy
def policy_gate(state: dict, deps: Deps) -> dict:
    requests = policy.approvals_needed(state["plan"], state["envelope"]["idempotency_key"],
                                       deps.settings)
    return {"requests": requests, "status": "waiting_for_user" if requests else "routine"}


# --------------------------------------------------------------------------- 5. apply
def apply_changes(state: dict, deps: Deps) -> dict:
    """The ONLY step that writes the knowledge graph. Idempotent."""
    repo, envelope = deps.repo, state["envelope"]
    key = envelope["idempotency_key"]
    if repo.already_applied(key):
        return {"status": "duplicate"}
    decisions = {d["request_id"]: d for d in state.get("decisions", [])}
    requests = {r["id"]: r for r in state.get("requests", [])}
    for rid in requests:
        if rid not in decisions:          # never auto-resolve a pending request (§7.3 GAP rule)
            return {"status": "waiting_for_user"}
    summary = {"content": envelope["file"]["rel_path"], "subject": None, "topics_added": [],
               "evidence_added": 0, "skipped": [], "decided_by_user": [], "autonomous": []}

    def decision_for(kind: str, ref: str | None = None):
        for r in requests.values():
            if r["kind"] == kind and (ref is None or ref in r["refs"]):
                return r, decisions[r["id"]]
        return None, None

    _store_content(repo, envelope, key)

    # --- subject
    p = state["plan"]
    subj_req, subj_dec = decision_for("new_subject")
    if subj_req is None:
        low_req, low_dec = decision_for("low_confidence", "subject:" + str(p["subject"]["existing_id"]))
        if low_dec is not None and low_dec["action"] == "reject":
            return _finish(repo, key, summary, msg("note.subject_rejected"), [low_req])
        subject = repo.subjects[p["subject"]["existing_id"]]
        if low_dec is not None:
            summary["decided_by_user"].append(low_req["id"])
    elif subj_dec["action"] == "reject":
        return _finish(repo, key, summary, msg("note.new_subject_rejected"),
                       [subj_req])
    else:
        correction = subj_dec.get("correction") or {}
        if correction.get("use_existing_id") in repo.subjects:
            subject = repo.subjects[correction["use_existing_id"]]
        else:
            name = correction.get("name") or p["subject"]["name"]
            subject = repo.find_subject(name) or repo.add_subject(
                name, subj_req["id"], msg("h.subject_approved"))
        summary["decided_by_user"].append(subj_req["id"])
    summary["subject"] = subject.name

    # --- study context: explicit classification (Collector) or one grouped question
    def reclassified(changed):
        for tp in topics_of_context(repo, changed.id):
            reassess(repo, deps.mastery, tp, msg("cause.classification"), [], deps.settings.language
                     if deps.settings else None)
    ctx = resolve_context(repo, envelope, subject.id, subject.name, reclassified)
    if not repo.already_applied(f"{key}:context"):
        count_file(repo, ctx)
        repo.mark_applied(f"{key}:context")
    # evidence from a file happened when the file was written, not when it was processed
    occurred = envelope_context(envelope)["modified_at"] or repo.clock()

    # --- topics
    major_req, major_dec = decision_for("major_graph")
    low_req, low_dec = decision_for("low_confidence")
    topic_ids: dict[str, str] = {}
    for t in p["topics"]:
        name = t["name"]
        mode, approval = "autonomous", None
        if t.get("conflict"):
            c_req, c_dec = decision_for("conflict", "topic:" + name)
            summary["decided_by_user"].append(c_req["id"])
            # approve = keep it as a topic of THIS subject; correct = under another name;
            # reject = change nothing.
            if c_dec["action"] == "reject":
                summary["skipped"].append(msg("skip.conflict", topic=name))
                continue
            if c_dec["action"] == "correct":
                name = c_dec["correction"].get("name") or name
            mode, approval = "approved", c_req["id"]
        if t["confidence"] < _low(deps):
            req, dec = decision_for("low_confidence", "topic:" + t["name"])
            if dec["action"] == "reject":
                summary["skipped"].append(msg("skip.rejected", topic=name))
                continue
            if dec["action"] == "correct" and dec["correction"].get("name"):
                name = dec["correction"]["name"]
            mode, approval = "approved", req["id"]
            summary["decided_by_user"].append(req["id"])
        existing = (repo.topics.get(t["existing_id"]) if t["existing_id"] else None) \
            or repo.find_topic(subject.id, name)
        if existing is None:
            if major_req is not None and t["confidence"] >= _low(deps) \
                    and not t.get("conflict"):
                summary["decided_by_user"].append(major_req["id"])
                if major_dec["action"] == "reject":
                    summary["skipped"].append(msg("skip.large_rejected", topic=name))
                    continue
                mode, approval = "approved", major_req["id"]
            existing = repo.add_topic(subject.id, name, msg("h.topic_identified"),
                                      mode, approval)
            summary["topics_added"].append(name)
        topic_ids[normalize_name(t["name"])] = existing.id

    # --- prerequisite edges between topics of this subject ([ASSUMPTION] autonomous;
    #     an edge that would contradict the existing order is NOT added)
    allow_edges = deps.settings.policy.autonomous_prerequisites if deps.settings else True
    for t in p["topics"]:
        if t["prerequisites"] and not allow_edges:
            summary["skipped"].append(msg("skip.order_setting", topic=t["name"]))
            continue
        dst = topic_ids.get(normalize_name(t["name"]))
        for pre in t["prerequisites"]:
            src = topic_ids.get(normalize_name(pre))
            if src is None:
                found = repo.find_topic(subject.id, pre)
                src = found.id if found else None
            if dst is None:
                continue
            if src is None:                      # the foundation topic is not known yet
                waiting = (subject.id, normalize_name(pre), dst)
                if waiting not in repo.pending_prerequisites:
                    repo.pending_prerequisites.append(waiting)
                continue
            if repo.would_create_cycle(src, dst):
                summary["skipped"].append(msg("skip.order_contradicts", src=pre, dst=t["name"]))
                continue
            if repo.add_edge(Edge(src, dst, "prerequisite"), msg("h.order_material"),
                             "autonomous"):
                summary["autonomous"].append(msg("tr.order_item", src=pre, dst=t["name"]))

    # --- spec §13: skills (partOf their topic) and related topics – routine structure (classify)
    for t in p["topics"]:
        topic_id = topic_ids.get(normalize_name(t["name"]))
        if topic_id is None:
            continue
        for name in t.get("skills", []):
            if name.strip() and repo.find_skill(topic_id, name) is None:
                repo.add_skill(topic_id, name, msg("h.skill_identified"), "autonomous")
        for other in t.get("related", []):
            oid = topic_ids.get(normalize_name(other)) or getattr(repo.find_topic(subject.id, other), "id", None)
            if oid and oid != topic_id:
                a, b = sorted((topic_id, oid))          # 'related' has no direction: stored once
                repo.add_edge(Edge(a, b, "related"), msg("h.related_identified"), "autonomous")

    # --- earlier proposals that waited for a topic which exists now
    if allow_edges:
        for waiting in list(repo.pending_prerequisites):
            subject_id, pre_name, dst = waiting
            found = repo.find_topic(subject_id, pre_name) if subject_id == subject.id else None
            if found is None or dst not in repo.topics:
                continue
            repo.pending_prerequisites.remove(waiting)
            if repo.would_create_cycle(found.id, dst):
                summary["skipped"].append(msg("skip.order_contradicts", src=found.name, dst=repo.topics[dst].name))
            elif repo.add_edge(Edge(found.id, dst, "prerequisite"), msg("h.order_late"),
                               "autonomous"):
                summary["autonomous"].append(msg("tr.order_item", src=found.name, dst=repo.topics[dst].name))

    # --- evidence (merge_evidence: APPROVED autonomous)
    added_by_topic: dict[str, list[Evidence]] = {}
    for i, e in enumerate(p["evidence"]):
        topic_id = topic_ids.get(normalize_name(e["topic"]))
        sub_key = f"{key}:evidence:{i}"
        if topic_id is None or repo.already_applied(sub_key):
            continue
        ev = Evidence(repo.new_id("ev"), topic_id, e["type"], e["authorship"],
                      chunk_key(envelope, e["chunk_id"]),
                      counts_as_user_ability=e["authorship"] in ("user", "user_modified"),
                      note=e.get("note", ""), occurred_at=occurred, source="file",
                      context_id=ctx.id,
                      skill_id=getattr(repo.find_skill(topic_id, e["skill"]), "id", None) if e.get("skill") else None)
        repo.add_evidence(ev, msg("h.evidence_material"), "autonomous")
        repo.mark_applied(sub_key)
        added_by_topic.setdefault(topic_id, []).append(ev)
        summary["evidence_added"] += 1

    # --- update_mastery (APPROVED autonomous). Formula OPEN: the model is pluggable.
    summary["mastery_changes"] = []
    for topic_id, evs in added_by_topic.items():
        change = reassess(repo, deps.mastery, repo.topics[topic_id], "cause.new_material",
                          [e.id for e in evs])
        if change:
            summary["mastery_changes"].append(msg(
                "tr.change", topic=repo.topics[topic_id].name, before_key=change["before"]["state"],
                after_key=change["after"]["state"]))
    summary["mastery_model"] = getattr(deps.mastery, "name", "placeholder")

    return _finish(repo, key, summary, None, [])


def chunk_key(envelope: dict, chunk_id: str) -> str:
    """Chunk ids are unique only inside one envelope; stored ids are namespaced by the
    file version so chunks of different files never overwrite each other."""
    return f"{envelope['file']['sha256'][:16]}:{chunk_id}"


def _store_content(repo: InMemoryRepository, envelope: dict, key: str) -> None:
    sub_key = f"{key}:content"
    if repo.already_applied(sub_key):
        return
    f = envelope["file"]
    item = ContentItem(envelope["source"]["source_id"], f["rel_path"], f["sha256"])
    chunks = [Chunk(chunk_key(envelope, c["chunk_id"]), f["sha256"], c["location"], c["text"])
              for c in envelope["chunks"]]
    if f["change"] == "moved" and f.get("previous_rel_path"):
        repo.mark_source_removed(item.source_id, f["previous_rel_path"])
    repo.store_content(item, chunks)
    repo.mark_applied(sub_key)


def _finish(repo, key: str, summary: dict, note: dict | None, decided: list[dict]) -> dict:
    for r in decided:
        summary["decided_by_user"].append(r["id"])
    if note:
        summary["note"] = note
    summary["decided_by_user"] = sorted(set(summary["decided_by_user"]))
    repo.mark_applied(key)
    return {"status": "applied", "summary": summary}


# --------------------------------------------------------------------------- removal
def apply_removal(state: dict, deps: Deps) -> dict:
    envelope = state["envelope"]
    changed = deps.repo.mark_source_removed(envelope["source"]["source_id"],
                                            envelope["file"]["rel_path"])
    deps.repo.mark_applied(envelope["idempotency_key"])
    return {"status": "applied",
            "summary": {"content": envelope["file"]["rel_path"],
                        "note": msg("note.source_removed") if changed else msg("note.nothing_to_remove")}}


# --------------------------------------------------------------------------- transparency
def transparency_text(summary: dict, lang: str | None = None) -> str:
    """Short, user-facing summary of what changed and why (spec §17, APPROVED).
    The summary holds message keys; the text is rendered in `lang` (default: Hebrew)."""
    parts = []
    if summary.get("note"):
        parts.append(render(summary["note"], lang) + ".")
    if summary.get("topics_added"):
        parts.append(t("tr.new_topics", lang, names=summary["topics_added"]))
    if summary.get("autonomous"):
        parts.append(t("tr.order", lang, items=summary["autonomous"]))
    if summary.get("evidence_added"):
        parts.append(t("tr.evidence", lang, n=summary["evidence_added"], subject=summary.get("subject") or ""))
    if summary.get("skipped"):
        parts.append(t("tr.not_changed", lang, items=summary["skipped"]))
    if summary.get("mastery_changes"):
        parts.append(t("tr.mastery", lang, model_key="model." + summary.get("mastery_model", "placeholder"),
                       items=summary["mastery_changes"]))
    elif summary.get("evidence_added"):
        parts.append(t("tr.mastery_same", lang))
    return " ".join(p for p in parts if p)


def _lang(deps: Deps) -> str | None:
    return getattr(deps.settings, "language", None)


# --------------------------------------------------------------------------- driver
def run_without_graph(envelope: dict, deps: Deps, decide=None, observe=None) -> dict:
    """Run all steps in order without LangGraph. `decide(requests) -> decisions` plays the
    user. `observe(step, seconds, state)` is an optional monitoring hook (evaluation uses it
    to collect METADATA ONLY – see brain.evaluation.telemetry)."""
    import time

    state: dict = {"envelope": envelope}

    def step(name, fn):
        started = time.perf_counter()
        state.update(fn(state, deps))
        if observe is not None:
            observe(name, time.perf_counter() - started, state)

    step("validate", validate)
    if state["status"] in ("rejected", "duplicate"):
        return state
    if state["status"] == "removal":
        step("apply_removal", apply_removal)
        return state
    step("analyze", analyze)
    if state.get("status") == "failed":
        return state
    step("plan", plan)
    step("policy_gate", policy_gate)
    if state["requests"]:
        if decide is None:
            return state
        decisions = decide(state["requests"])
        for d in decisions:
            contracts.validate(contracts.DECISION, d)
        state["decisions"] = decisions
    step("apply_changes", apply_changes)
    if "summary" in state:
        state["transparency"] = transparency_text(state["summary"], _lang(deps))
    return state
