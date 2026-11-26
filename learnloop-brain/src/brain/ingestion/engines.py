"""Two interchangeable ways to RUN the same ingestion steps.

- LangGraphEngine – the approved engine (LangGraph, with checkpointer, interrupt, resume).
- PlainEngine     – the same steps and the same pause/resume behaviour without LangGraph,
                    keeping paused states in memory. For development and for environments
                    where langgraph is not installed. [RECOMMENDATION]

Both return (result, pending_requests). The Supervisor does not know which one runs.
"""

from __future__ import annotations

import copy

from brain import contracts
from brain.ingestion import steps


class LangGraphEngine:
    name = "langgraph"

    def __init__(self, deps: steps.Deps, checkpointer=None):
        from brain.ingestion import graph as ingestion
        if checkpointer is None:
            from langgraph.checkpoint.memory import InMemorySaver
            checkpointer = InMemorySaver()
        self._g = ingestion
        self.graph = ingestion.build_graph(deps, checkpointer)

    def start(self, envelope: dict, thread: str):
        result = self._g.start(self.graph, envelope, thread)
        return result, self._g.pending_requests(result)

    def resume(self, thread: str, decisions: list[dict]):
        result = self._g.resume(self.graph, thread, decisions)
        return result, self._g.pending_requests(result)


class PlainEngine:
    name = "plain"

    def __init__(self, deps: steps.Deps):
        self.deps = deps
        self._paused: dict[str, dict] = {}

    def start(self, envelope: dict, thread: str):
        state = steps.run_without_graph(envelope, self.deps, decide=None)
        if state.get("status") == "waiting_for_user":
            self._paused[thread] = copy.deepcopy(state)
            return state, state["requests"]
        return state, []

    def resume(self, thread: str, decisions: list[dict]):
        state = self._paused.pop(thread)
        known = {r["id"] for r in state["requests"]}
        merged = {d["request_id"]: d for d in state.get("decisions", [])}
        for d in decisions:
            try:
                contracts.validate(contracts.DECISION, d)
            except contracts.ContractError:
                continue
            if d["request_id"] in known:
                merged[d["request_id"]] = d
        state["decisions"] = list(merged.values())
        if not all(r["id"] in merged for r in state["requests"]):
            self._paused[thread] = state
            return state, state["requests"]
        state.update(steps.apply_changes(state, self.deps))
        state["transparency"] = steps.transparency_text(state.get("summary", {}), steps._lang(self.deps))
        return state, []
