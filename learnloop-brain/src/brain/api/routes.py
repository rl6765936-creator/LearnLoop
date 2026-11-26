"""HTTP routes as a plain function: (method, path, body) -> (status, JSON-able dict).

No web framework: the backend framework is [OPEN]. Any framework (or the small stdlib server
in server.py) only has to call `handle`. Errors never return stack traces or content.
"""

from __future__ import annotations

import re

from brain.activities import ActivityError
from brain.contracts import ContractError
from brain.i18n import t
from brain.providers.base import ProviderError
from brain.settings import describe

ROUTES = []


def route(method: str, pattern: str):
    def wrap(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return wrap


def handle(app, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    path = path.split("?", 1)[0].rstrip("/") or "/"
    for m, rx, fn in ROUTES:
        match = rx.match(path)
        if match and m == method:
            try:
                return 200, fn(app, body or {}, **match.groupdict())
            except (ActivityError, ContractError, ValueError) as e:
                return 400, {"error": str(e)[:200]}
            except ProviderError as e:
                # kind only – provider errors never contain content or keys
                if e.kind == "budget_exhausted":
                    return 503, {"error": "budget_exhausted", "message": t("budget.exhausted", app.lang)}
                return 503, {"error": e.kind}
            except KeyError:
                return 404, {"error": "not found"}
    return 404, {"error": "unknown route"}


@route("GET", "/api/subjects")
def subjects(app, body):
    return {"subjects": app.subject_spaces()}


@route("GET", r"/api/subjects/(?P<sid>[\w-]+)/dashboard")
def dashboard(app, body, sid):
    return app.dashboard(sid)


@route("GET", r"/api/topics/(?P<tid>[\w-]+)/history")
def history(app, body, tid):
    app.repo.topics[tid]
    return {"history": app.topic_history(tid)}


@route("GET", "/api/approvals")
def approvals(app, body):
    return {"approvals": app.pending_approvals()}


@route("POST", r"/api/approvals/(?P<rid>[\w-]+)")
def decide(app, body, rid):
    result = app.decide(rid, body.get("action", ""), body.get("correction"))
    app.commit()
    return result


@route("GET", "/api/ai-budget")
def ai_budget(app, body):
    """Spending of the monthly AI budget – numbers only, never the key or the provider config."""
    budget = getattr(app.supervisor.deps.model, "budget", None)
    return {"budget": budget.status() if budget else None}


@route("GET", "/api/system")
def system(app, body):
    """One screen of system state (no content, no key): AI status + budget, queue, sources,
    ActivityWatch time, recent manual uploads."""
    model = app.supervisor.deps.model
    hold = getattr(model, "hold_reason", lambda: None)()
    budget = getattr(model, "budget", None)
    inbox = app.ingestion.inbox
    by_source = {}
    for env in inbox:
        kind = env.get("source", {}).get("type", "folder")
        by_source[kind] = by_source.get(kind, 0) + 1
    uploads = sorted({c.rel_path[len("uploads/"):] for c in app.repo.content.values()
                      if c.source_id == "manual" and c.rel_path.startswith("uploads/")}
                     | {e["file"]["rel_path"][len("uploads/"):] for e in inbox
                        if e.get("source", {}).get("type") == "manual"})
    return {"mode": getattr(app, "mode", "demo"),
            "ai": {"state": "held" if hold else "active", "hold_reason": hold,
                   "credit_exhausted": bool(getattr(model, "credit_exhausted", lambda: False)()),
                   "budget": budget.status() if budget else None},
            "queue": {"waiting": len(inbox), "by_source": by_source},
            "last_cycle_at": app.last_cycle_at,
            "activity": app.activity_overview(),
            "uploads": uploads[-20:],
            "questions": app.activity_questions() if hasattr(app, "activity_questions") else []}


@route("POST", "/api/activity/questions")
def answer_activity(app, body):
    return app.answer_activity(str(body.get("app", "")), str(body.get("answer", "")))


@route("GET", "/api/activity")
def activity(app, body):
    """ActivityWatch: minutes per app (and her category) over the last 7 days. General tags only."""
    return app.activity_overview()


@route("GET", "/api/strengths")
def all_strengths(app, body):
    return {"strengths": app.strengths()}


@route("GET", "/api/schedule")
def schedule(app, body):
    from brain.scheduler import CycleScheduler
    sched = getattr(app, "scheduler", None) or CycleScheduler(app)
    return sched.status()


@route("GET", "/api/classifications")
def classifications(app, body):
    return {"classifications": app.pending_classifications()}


@route("POST", r"/api/classifications/(?P<qid>[\w-]+)")
def classify(app, body, qid):
    result = app.classify(qid, body.get("study_status", ""), body.get("period"))
    app.commit()
    return result


@route("POST", r"/api/subjects/(?P<sid>[\w-]+)/track")
def start_track(app, body, sid):
    app.repo.subjects[sid]
    result = app.start_track(sid, body.get("activity_type"))
    app.commit()
    return result


@route("POST", "/api/cycle")
def cycle(app, body):
    result = app.run_cycle()
    app.commit()
    return result


@route("GET", "/api/next")
def next_step(app, body):
    return {"next": app.next_step(body.get("subject_id"))}


@route("GET", r"/api/subjects/(?P<sid>[\w-]+)/next")
def next_for_subject(app, body, sid):
    return {"next": app.next_step(sid)}


@route("POST", "/api/activities")
def create_activity(app, body):
    view = app.start_activity(body["topic_id"], body["activity_type"], body.get("purpose", "learn"))
    app.commit()
    return view


@route("POST", r"/api/activities/(?P<aid>[\w-]+)/continue")
def continue_activity(app, body, aid):
    return app.continue_activity(aid)


@route("POST", r"/api/activities/(?P<aid>[\w-]+)/answer")
def answer(app, body, aid):
    result = app.answer(aid, body["item_id"], body.get("answer"))
    app.commit()
    return result


@route("POST", r"/api/activities/(?P<aid>[\w-]+)/hint")
def hint(app, body, aid):
    text = app.hint(aid, body["item_id"])
    app.commit()
    return {"hint": text}


@route("POST", r"/api/activities/(?P<aid>[\w-]+)/solution")
def solution(app, body, aid):
    text = app.show_solution(aid, body["item_id"])
    app.commit()
    return {"solution": text}


@route("POST", r"/api/activities/(?P<aid>[\w-]+)/finish")
def finish(app, body, aid):
    result = app.finish_activity(aid)
    app.commit()
    return result


@route("POST", r"/api/activities/(?P<aid>[\w-]+)/skip")
def skip(app, body, aid):
    result = app.skip_activity(aid)
    app.commit()
    return result


@route("GET", "/api/settings")
def settings(app, body):
    return {"settings": [dict(r, value=r["value"] if not isinstance(r["value"], dict)
                              else {str(k): v for k, v in r["value"].items()}) for r in describe(app.settings)]}


@route("GET", "/api/log")
def log(app, body):
    return {"log": app.cycle_log[-50:]}
