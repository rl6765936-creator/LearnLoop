"""The ingestion workflow as a LangGraph graph (brain-design §6.2).

LangGraph terms used here:
- node        = one step (the functions in steps.py)
- edge        = which step comes next; conditional edge = chosen by the result
- state       = the dict below, saved after every step by the checkpointer
- interrupt() = pause and wait for the user; the state is saved and the graph stops
- Command(resume=...) = continue the paused thread with the user's answer.
  NOTE: on resume the paused node runs again FROM ITS START. human_review therefore
  does nothing before interrupt() – no writes, no model calls.

Requires the `langgraph` package (installed in this project's venv only).
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from brain import contracts
from brain.ingestion import steps


class IngestState(TypedDict, total=False):
    envelope: dict
    status: str
    error: str
    attempts: int
    analysis: dict
    plan: dict
    requests: list
    decisions: list
    summary: dict
    transparency: str


def build_graph(deps: steps.Deps, checkpointer):
    g = StateGraph(IngestState)

    g.add_node("validate", lambda s: steps.validate(s, deps))
    g.add_node("analyze", lambda s: steps.analyze(s, deps))
    g.add_node("plan", lambda s: steps.plan(s, deps))
    g.add_node("policy_gate", lambda s: steps.policy_gate(s, deps))
    g.add_node("human_review", _human_review)
    g.add_node("apply_changes", lambda s: steps.apply_changes(s, deps))
    g.add_node("apply_removal", lambda s: steps.apply_removal(s, deps))
    g.add_node("transparency", lambda s: {"transparency": steps.transparency_text(
        s.get("summary", {}), steps._lang(deps))})

    g.add_edge(START, "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"analyze": "analyze", "removal": "apply_removal", "end": END})
    g.add_conditional_edges("analyze", lambda s: "end" if s.get("status") == "failed" else "plan",
                            {"plan": "plan", "end": END})
    g.add_edge("plan", "policy_gate")
    g.add_conditional_edges("policy_gate", lambda s: "review" if s["requests"] else "apply",
                            {"review": "human_review", "apply": "apply_changes"})
    g.add_conditional_edges("human_review", _all_decided,
                            {"apply": "apply_changes", "again": "human_review"})
    g.add_edge("apply_changes", "transparency")
    g.add_edge("apply_removal", "transparency")
    g.add_edge("transparency", END)
    return g.compile(checkpointer=checkpointer)


def _after_validate(s: IngestState) -> str:
    if s["status"] == "received":
        return "analyze"
    if s["status"] == "removal":
        return "removal"
    return "end"                                   # rejected / duplicate: nothing processed


def _human_review(s: IngestState) -> dict:
    # Payload must be JSON-serializable: ids and short texts only, never objects.
    answer = interrupt({"requests": s["requests"]})
    known = {r["id"] for r in s["requests"]}
    merged = {d["request_id"]: d for d in s.get("decisions", [])}
    for d in answer if isinstance(answer, list) else []:
        try:
            contracts.validate(contracts.DECISION, d)
        except contracts.ContractError:
            continue                               # invalid answer is ignored, request stays open
        if d["request_id"] in known:
            merged[d["request_id"]] = d
    return {"decisions": list(merged.values())}


def _all_decided(s: IngestState) -> str:
    decided = {d["request_id"] for d in s.get("decisions", [])}
    return "apply" if all(r["id"] in decided for r in s["requests"]) else "again"


# ----------------------------------------------------------------- small helpers
# Every key a previous run on the same thread may have left behind. A new run resets them,
# so old requests or decisions can never be reused for new content (e.g. a retry after a
# failed model call runs on the same thread).
_RESET = {"status": None, "error": None, "attempts": 0, "requests": [], "decisions": [],
          "summary": {}, "transparency": ""}


def start(graph, envelope: dict, thread_id: str) -> dict:
    return graph.invoke(dict(_RESET, envelope=envelope), {"configurable": {"thread_id": thread_id}})


def resume(graph, thread_id: str, decisions: list[dict]) -> dict:
    return graph.invoke(Command(resume=decisions), {"configurable": {"thread_id": thread_id}})


def pending_requests(result: dict) -> list[dict]:
    """Requests the graph is waiting on, from an invoke() result ([] if not paused)."""
    interrupts = result.get("__interrupt__") or []
    return [r for i in interrupts for r in (i.value or {}).get("requests", [])]
