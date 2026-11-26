"""LearnLoopApp – the brain as ONE application service.

Connects: Ingestion API -> Supervisor (ingestion workflow, approvals) -> knowledge graph
-> understanding assessment -> activities -> next step. The user interface and an HTTP
layer only talk to this class. Everything runs with the FakeModel and synthetic data
until a model provider is chosen (Q-A).
"""

from __future__ import annotations

import json

from collections import Counter
from datetime import datetime, timezone

from brain.activities import ActivityService
from brain.domain.mastery import model_from_settings, reassess
from brain.domain import study
from brain.domain.strengths import strengths
from brain.domain.repository import InMemoryRepository
from brain.i18n import msg, render, t
from brain.ingestion.engines import LangGraphEngine, PlainEngine
from brain.ingestion.steps import Deps
from brain.ingestion_api import IngestionService
from brain.settings import Settings
from brain.supervisor import Supervisor


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LearnLoopApp:
    def __init__(self, model, settings: Settings | None = None, clock=_utc_now,
                 engine: str = "langgraph", repo: InMemoryRepository | None = None, store=None):
        """store: brain.storage.store.Store – loads the saved state now; commit() saves."""
        self.settings = settings or Settings()
        self.repo = repo or InMemoryRepository(clock=clock)
        self.mastery = model_from_settings(self.settings.mastery)
        self.lang = self.settings.language
        self.deps = Deps(repo=self.repo, model=model, mastery=self.mastery, settings=self.settings)
        eng = LangGraphEngine(self.deps) if engine == "langgraph" else PlainEngine(self.deps)
        self.supervisor = Supervisor(self.deps, engine=eng)
        self.ingestion = IngestionService(self.repo)
        self.last_cycle_at: str | None = None
        self.collector_grants: dict[str, str | None] = {}
        self.activity_days: dict[str, list] = {}     # ActivityWatch: day -> [{app, category, minutes}]
        self.app_links: dict[str, str] = {}          # app -> subject_id | "not_study" | "skipped" (her answers)
        self.activities = ActivityService(self.repo, model, self.mastery, self.settings)
        self.cycle_log: list[str] = []
        self.store = store
        if store is not None:
            store.load(self)

    def commit(self) -> None:
        """Persist everything (one transaction). No-op without a store."""
        if self.store is not None:
            self.store.save(self)

    # ------------------------------------------------------------ permissions
    def set_transfer_permission(self, source_id: str, allowed: bool) -> None:
        """Server-side copy of the user's per_source transfer approval (spec §27)."""
        self.repo.set_transfer_permission(source_id, allowed)
        self.repo.record("permission", source_id, "transfer_" + ("allowed" if allowed else "revoked"),
                         None, {"allowed": allowed}, msg("h.permission"), "user")

    # ------------------------------------------------------------ ingestion
    def receive(self, raw) -> dict:
        return self.ingestion.receive(raw)

    # ------------------------------------------------------------ local Collector link (Q-D)
    def receive_from_collector(self, raw) -> dict:
        """The local Collector already checked the user's per_source transfer permission (spec §27)
        before sending. The brain keeps its own copy, bound to the Collector's grant: a NEW grant
        for the same source after a revoke is accepted again only through a new transfer."""
        try:
            env = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
            source, grant = env["source"]["source_id"], env["source"].get("grant_id")
        except (ValueError, KeyError, TypeError):
            return {"accepted": False, "reason": "not valid JSON"}
        known = self.collector_grants.get(source)
        if known != grant:
            self.collector_grants[source] = grant
            self.set_transfer_permission(source, True)
        return self.receive(env)

    def revoke_from_collector(self, source_id: str) -> None:
        self.collector_grants.pop(source_id, None)
        self.set_transfer_permission(source_id, False)

    def receive_activity(self, raw) -> dict:
        """ActivityWatch day summary (Q12): app name, category, minutes only – checked against the
        contract, so nothing else (titles, URLs) can be stored. No AI call: general tags only (§26).
        A later summary of the same day replaces the earlier one."""
        from brain import contracts
        try:
            env = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
            contracts.validate("activity_summary", env)
        except (ValueError, contracts.ContractError):
            return {"accepted": False, "reason": "not a valid activity summary"}
        self.activity_days[env["day"]] = env["apps"]
        for day in sorted(self.activity_days)[:-90]:              # keep the last 90 days
            self.activity_days.pop(day)
        return {"accepted": True}

    def activity_overview(self, days: int = 7) -> dict:
        recent = sorted(self.activity_days)[-days:]
        totals: dict[tuple, int] = {}
        for d in recent:
            for a in self.activity_days[d]:
                key = (a["app"], a["category"])
                totals[key] = totals.get(key, 0) + a["minutes"]
        rows = [{"app": a, "category": c, "minutes": m} for (a, c), m in sorted(totals.items(), key=lambda x: -x[1])]
        return {"days": recent, "apps": rows[:20]}

    # spec §26: ActivityWatch gives general tags only – when it is unclear what she studied, ASK.
    ASK_MINUTES = 60          # [ASSUMPTION] at least an hour in the last 7 days
    ASK_AT_ONCE = 3           # at most three questions at a time (no burden)

    def activity_questions(self) -> list[dict]:
        overview = self.activity_overview()
        per_app: dict[str, int] = {}
        for row in overview["apps"]:
            per_app[row["app"]] = per_app.get(row["app"], 0) + row["minutes"]
        subjects = [{"id": s.id, "name": s.name} for s in self.repo.subjects.values()]
        out = [{"app": a, "minutes": m, "subjects": subjects}
               for a, m in sorted(per_app.items(), key=lambda x: -x[1])
               if m >= self.ASK_MINUTES and a not in self.app_links]
        return out[: self.ASK_AT_ONCE]

    def answer_activity(self, app_name: str, answer: str) -> dict:
        """answer: a subject id, "not_study" or "skipped". Asked once; she can change it later.
        Time in an app is NEVER evidence of understanding (§5) – it only shows working time."""
        if not app_name or (answer not in ("not_study", "skipped") and answer not in self.repo.subjects):
            raise ValueError("unknown app or answer")
        before = self.app_links.get(app_name)
        self.app_links[app_name] = answer
        self.repo.record("activity_link", app_name, "updated", before, answer, msg("h.activity_link"), "user")
        return {"ok": True}

    def subject_minutes(self, subject_id: str) -> int:
        return sum(a["minutes"] for d in sorted(self.activity_days)[-7:] for a in self.activity_days[d]
                   if self.app_links.get(a["app"]) == subject_id)

    def decided_classifications(self) -> list[dict]:
        """Classification answers given HERE, for the Collector (decision 10) – folder, status,
        period and source only; no material."""
        out = []
        for ctx in self.repo.study_contexts.values():
            if ctx.decided_by == "user_decision":
                out.append({"source_id": ctx.source_id, "collection": ctx.collection,
                            "study_status": ctx.study_status, "period": ctx.period})
        return out

    def run_cycle(self, trigger: str = "user_request") -> dict:
        """trigger: user_request (explicit – spec §35 early trigger) | scheduled | accumulation."""
        self.last_cycle_at = self.repo.clock()
        hold = getattr(self.supervisor.deps.model, "hold_reason", lambda: None)()
        if hold:
            # Live mode without an approved provider: material WAITS (encrypted); nothing is sent.
            waiting = len(self.ingestion.inbox)
            self.repo.record("cycle", trigger, "held", None, {"waiting": waiting, "reason": hold},
                             msg("h.ai_not_ready"), "autonomous")
            return {"processed": 0, "waiting": waiting, "failed": 0, "rejected": [], "duplicates": 0,
                    "held": hold, "summary": t("ai.not_ready", self.lang)}
        exhausted = getattr(self.supervisor.deps.model, "budget_exhausted", lambda: False)()
        if exhausted:
            # Decision 2026-11-24: budget used up -> no AI calls until next month; the queue is KEPT.
            waiting = len(self.ingestion.inbox)
            self.repo.record("cycle", trigger, "budget_paused", None, {"waiting": waiting},
                             msg("h.budget_paused"), "autonomous")
            credit = getattr(self.supervisor.deps.model, "credit_exhausted", lambda: False)()
            return {"processed": 0, "waiting": waiting, "failed": 0, "rejected": [], "duplicates": 0,
                    "budget_exhausted": True,
                    "summary": t("credit.exhausted" if credit else "budget.exhausted", self.lang)}
        batch = self.ingestion.take()
        summary = self.supervisor.run_cycle([{"type": "content", "envelope": e} for e in batch])
        failed = set(summary.failed)
        self.ingestion.put_back([e for e in batch if e["file"]["rel_path"] in failed])
        text = summary.text(self.lang)
        self.cycle_log.append(text)
        if batch:
            self.repo.record("cycle", trigger, "ran", None, {"files": len(batch)}, msg("h.cycle_ran", trigger_key=f"trigger.{trigger}"),
                             "user" if trigger == "user_request" else "autonomous")
        return {"processed": len(batch), "waiting": len(summary.waiting), "failed": len(summary.failed),
                "rejected": summary.rejected, "duplicates": summary.duplicates, "summary": text}

    # ------------------------------------------------------------ approvals
    def pending_approvals(self) -> list[dict]:
        return [dict({k: r[k] for k in ("id", "kind", "options", "count")},
                     summary=render(r.get("summary_msg") or r["summary"], self.lang))
                for r in self.supervisor.center.pending_grouped()]

    def decide(self, request_id: str, action: str, correction: dict | None = None) -> dict:
        result = self.supervisor.decide(request_id, action, correction)
        if result is None:
            return {"status": "waiting_for_other_decisions"}
        if result.get("transparency"):
            self.cycle_log.append(result["transparency"])
        return {"status": result.get("status"), "summary": result.get("transparency", "")}

    def _project_progress(self, subject_id: str) -> dict:
        """spec §41: project progress in the subject dashboard."""
        projects = [a for a in self.repo.activities.values() if a.subject_id == subject_id and a.type == "project"]
        items = sum(len(a.content["items"]) for a in projects)
        answered = sum(1 for a in projects for e in a.answers.values() if "score" in e)
        last = max(projects, key=lambda a: a.created_at, default=None)
        return {"projects": len(projects), "finished": sum(a.status == "finished" for a in projects),
                "items_answered": answered, "items_total": items,
                "last_title": last.content["title"] if last else None}

    def strengths(self) -> list[dict]:
        """spec §42: evidence-based strengths across all subjects."""
        return strengths(self.repo, self.settings, None, self.lang)

    # ------------------------------------------------------------ study classification
    def pending_classifications(self) -> list[dict]:
        """Grouped by subject (never per file). Non-blocking: material is processed meanwhile."""
        return study.pending_questions(self.repo)

    def classify(self, request_id: str, study_status: str, period: str | None = None) -> dict:
        def reclassified(ctx):
            for tp in study.topics_of_context(self.repo, ctx.id):
                reassess(self.repo, self.mastery, tp, msg("cause.classification"), [], self.lang)
        return study.decide(self.repo, request_id, study_status, period, reclassified)

    # ------------------------------------------------------------ knowledge views
    def subject_spaces(self) -> list[dict]:
        indicators = self.activities.subject_indicators()
        out = []
        for s in self.repo.subjects.values():
            states = Counter(t.state for t in self.repo.topics.values() if t.subject_id == s.id)
            out.append({"id": s.id, "name": s.name, "indicator": indicators.get(s.id),
                        "topics": sum(states.values()), "states": dict(states),
                        "periods": study.subject_periods(self.repo, s.id)})
        return out

    def dashboard(self, subject_id: str) -> dict:
        """Progress overview of one subject (spec §41)."""
        topics = [t for t in self.repo.topics.values() if t.subject_id == subject_id]
        ids = {t.id for t in topics}
        scored = [t.score for t in topics if t.score is not None]
        return {
            "subject": self.repo.subjects[subject_id].name,
            "subject_id": subject_id,
            "learned": [t.name for t in topics if t.state == "understood"],
            "in_progress": [t.name for t in topics if t.state in ("learning", "reinforce")],
            # 'new' = no evidence in the system yet – NOT proof that she never studied it
            "no_evidence_yet": [t.name for t in topics if t.state == "new"],
            "periods": study.subject_periods(self.repo, subject_id),
            "needs_reinforcement": [t.name for t in topics if t.state == "reinforce"],
            "estimated_mastery": round(sum(scored) / len(scored), 2) if scored else None,
            "assessment_model": self.mastery.name,        # 'bayesian' = the approved formula
            "show_confidence": self.settings.mastery.show_confidence,
            "topics": [{"id": t.id, "name": t.name, "state": t.state, "score": t.score,
                        "confidence": t.confidence,
                        "study": study.topic_study(self.repo, t.id),
                        "skills": sorted(k.name for k in self.repo.skills.values() if k.topic_id == t.id),
                        "assessment": study.assessment_status(self.repo, t.id),
                        "evidence": sum(1 for e in self.repo.evidence.values() if e.topic_id == t.id)}
                       for t in sorted(topics, key=lambda t: t.name)],
            "skill_map": [{"from": self.repo.topics[e.src].name, "to": self.repo.topics[e.dst].name,
                           "kind": e.kind} for e in self.repo.edges if e.src in ids and e.dst in ids]
                         + [{"from": self.repo.skills[e.src].name, "to": self.repo.topics[e.dst].name, "kind": "partOf"}
                            for e in self.repo.edges if e.kind == "partOf" and e.src in self.repo.skills
                            and e.dst in ids],
            "timeline": [{"at": r.at, "what": f"{r.entity} {r.action}", "mode": r.mode,
                          "why": render(r.reason, self.lang)}
                         for r in self.repo.history
                         if r.entity_id in ids or (r.entity == "evidence" and (r.after or {}).get("topic_id") in ids)],
            "indicator": self.activities.subject_indicators().get(subject_id),
            "completed_track": self.activities.track_status(subject_id),
            "project_progress": self._project_progress(subject_id),
            "work_minutes_7d": self.subject_minutes(subject_id),     # ActivityWatch, by her app links
            "strengths": strengths(self.repo, self.settings, subject_id, self.lang),
        }

    def topic_history(self, topic_id: str) -> list[dict]:
        return [{"seq": r.seq, "at": r.at, "entity": r.entity, "action": r.action, "mode": r.mode,
                 "reason": render(r.reason, self.lang), "before": r.before, "after": r.after}
                for r in self.repo.timeline(topic_id)]

    # ------------------------------------------------------------ activities
    def next_step(self, subject_id: str | None = None) -> dict | None:
        return self.activities.recommend(subject_id)

    def start_activity(self, topic_id: str, activity_type: str, purpose: str = "learn") -> dict:
        return self.activities.start(self.activities.create(topic_id, activity_type, purpose).id)

    def start_track(self, subject_id: str, activity_type: str | None = None) -> dict:
        """The due step of the completed-material track (spec §21): broad quiz, project/activity,
        weekly refresher. Optional – ignoring it never costs anything."""
        return self.activities.start(self.activities.create_track(subject_id, activity_type).id)

    def continue_activity(self, activity_id: str) -> dict:
        return self.activities.start(activity_id)

    def answer(self, activity_id: str, item_id: str, answer) -> dict:
        return self.activities.answer(activity_id, item_id, answer)

    def hint(self, activity_id: str, item_id: str) -> str:
        return self.activities.hint(activity_id, item_id)

    def show_solution(self, activity_id: str, item_id: str) -> str:
        return self.activities.show_solution(activity_id, item_id)

    def finish_activity(self, activity_id: str) -> dict:
        return self.activities.finish(activity_id)

    def skip_activity(self, activity_id: str) -> dict:
        return self.activities.skip(activity_id)
