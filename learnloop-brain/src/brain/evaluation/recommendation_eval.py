"""Recommendation quality (spec §44, APPROVED metric area) on synthetic scenarios.

Each scenario builds a small learning state with the FakeModel and checks the next-step rule
the spec approves for it. Score = share of scenarios where the recommendation is right.
  - §18 a missing prerequisite comes first
  - §19 reinforcement follows the type of mistake
  - §6  an unfinished activity is offered to continue
  - §20 ignoring a suggestion changes nothing
  - §21 a subject already studied starts with a broad quiz, not from zero
  - §18 an understood topic whose last check is old gets a retention check
"""

from __future__ import annotations

from datetime import datetime, timedelta

T0 = "2026-11-24T08:00:00+00:00"
SOURCE = "folder-eval"


def _app(script, now, context=None):
    from brain.app import LearnLoopApp
    from brain.model_gateway import FakeModel
    app = LearnLoopApp(FakeModel(script), clock=lambda: now[0], engine="plain")
    app.set_transfer_permission(SOURCE, True)
    env = {"schema_version": 1, "envelope_id": "env-eval", "idempotency_key": "e" * 64,
           "source": {"type": "folder", "source_id": SOURCE, "grant_id": "g"},
           "file": {"rel_path": "a.md", "sha256": "0" * 64, "size": 9, "change": "new"},
           "chunks": [{"chunk_id": "c0", "index": 0, "total": 1, "location": "lines 1-1", "text": "synthetic"}],
           "filter": {"status": "clean", "redactions": []}, "extract": {"reader": "text", "warnings": []},
           "content_trust": "untrusted_user_file"}
    if context:
        env["context"] = context
    app.receive(env)
    app.run_cycle()
    for p in app.pending_approvals():
        app.decide(p["id"], "approve")
    return app


def _topics(*names, prereq=None):
    prereq = prereq or {}
    return {"subject": {"name": "S", "matched_subject_id": None, "confidence": 0.9},
            "topics": [{"name": n, "confidence": 0.9, "chunk_ids": ["c0"], "prerequisites": prereq.get(n, [])}
                       for n in names],
            "evidence": [{"topic": n, "type": "exposure", "authorship": "unknown", "chunk_id": "c0"} for n in names]}


def _scenarios():
    def prerequisite_first():
        now = [T0]
        app = _app(_topics("Basics", "Advanced", prereq={"Advanced": ["Basics"]}), now)
        rec = app.next_step(next(iter(app.repo.subjects)))
        return app.repo.topics[rec["topic_id"]].name == "Basics"

    def mistake_type():
        now = [T0]
        app = _app(_topics("Lists"), now)
        tid = next(iter(app.repo.topics))
        app.activities.model.grade = lambda i, a, lang="he": {"score": 0.1, "feedback": "x",
                                                              "mistake_type": "misconception"}
        for _ in range(3):
            act = app.start_activity(tid, "quiz", "learn")
            for item in act["items"]:
                app.answer(act["id"], item["id"], 0 if item["kind"] == "multiple_choice" else "no")
            app.finish_activity(act["id"])
        rec = app.next_step(app.repo.topics[tid].subject_id)
        return rec["activity_type"] == "summary"

    def continue_unfinished():
        now = [T0]
        app = _app(_topics("Lists"), now)
        act = app.start_activity(next(iter(app.repo.topics)), "quiz", "learn")
        return app.next_step(None)["action"] == "continue" and app.next_step(None)["activity_id"] == act["id"]

    def ignoring_costs_nothing():
        now = [T0]
        app = _app(_topics("Lists"), now)
        before = [(t.state, t.score) for t in app.repo.topics.values()]
        app.next_step(None)
        act = app.start_activity(next(iter(app.repo.topics)), "quiz", "learn")
        app.skip_activity(act["id"])
        return [(t.state, t.score) for t in app.repo.topics.values()] == before

    def already_studied_broad_quiz():
        now = [T0]
        app = _app(_topics("Stack", "Queue"), now, {"collection": "y1", "study_status": "completed", "period": None,
                                                    "queue": "backfill", "modified_at": "2025-01-01T00:00:00+00:00"})
        rec = app.next_step(next(iter(app.repo.subjects)))
        return rec["action"] == "start_track" and rec["step"] == "placement"

    def retention_when_old():
        now = [T0]
        app = _app(_topics("Lists"), now)
        tid = next(iter(app.repo.topics))
        app.activities.model.grade = lambda i, a, lang="he": {"score": 1.0, "feedback": "x", "mistake_type": "none"}
        for kind in ("quiz", "challenge", "quiz"):
            act = app.start_activity(tid, kind, "learn")
            for item in act["items"]:
                app.answer(act["id"], item["id"], item.get("answer_index", 1) if item["kind"] == "multiple_choice"
                           else ({x: y for x, y in item["pairs"]} if item["kind"] == "match" else "good"))
            app.finish_activity(act["id"])
        if app.repo.topics[tid].state != "understood":
            return False
        now[0] = (datetime.fromisoformat(T0) + timedelta(days=60)).isoformat()
        return app.next_step(None)["purpose"] == "retention"

    return {"prerequisite_first": prerequisite_first, "mistake_type": mistake_type,
            "continue_unfinished": continue_unfinished, "ignoring_costs_nothing": ignoring_costs_nothing,
            "already_studied_broad_quiz": already_studied_broad_quiz, "retention_when_old": retention_when_old}


def recommendation_quality() -> tuple[float, list[str]]:
    failed = []
    scen = _scenarios()
    for name, check in scen.items():
        try:
            ok = check()
        except Exception:                        # noqa: BLE001 – a crash is a failed scenario
            ok = False
        if not ok:
            failed.append(name)
    return round(1 - len(failed) / len(scen), 4), failed
