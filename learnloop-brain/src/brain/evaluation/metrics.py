"""Run the synthetic scenarios and measure the spec §44 areas (APPROVED list).

Measured: classification quality, HITL behaviour, privacy protection, reliability,
mastery assessment quality (approved scenario table) and recommendation quality
(recommendation_eval.py – the approved next-step rules on synthetic states).
"""

from __future__ import annotations

from brain.domain.models import normalize_name
from brain.domain.repository import InMemoryRepository
from brain.evaluation.dataset import SOURCE, Scenario, scenarios
from brain.domain.mastery import model_from_settings
from brain.evaluation.mastery_eval import mastery_quality
from brain.evaluation.recommendation_eval import recommendation_quality
from brain.evaluation.telemetry import MetadataRecorder, find_leaks
from brain.ingestion.steps import Deps, run_without_graph


class ScriptedModel:
    """FakeModel variant for scenarios: one scripted answer per call."""
    name = "scripted"

    def __init__(self, answers: list, repo: InMemoryRepository):
        self.answers, self.repo, self.calls = list(answers), repo, 0

    def analyze(self, chunks, known_subjects, file_context=None):
        self.calls += 1
        item = self.answers.pop(0) if self.answers else self.answers_exhausted()
        if isinstance(item, Exception):
            raise item
        return item(self.repo) if callable(item) else item

    @staticmethod
    def answers_exhausted():
        raise RuntimeError("scenario has no more scripted answers")


def run_scenario(sc: Scenario, recorder: MetadataRecorder) -> dict:
    repo = InMemoryRepository(clock=lambda: "2026-11-24T00:00:00+00:00")
    repo.set_transfer_permission(SOURCE, sc.permitted)
    sc.setup(repo)
    deps = Deps(repo=repo, model=ScriptedModel(sc.answers, repo))
    recorder.set_scenario(sc.id)
    written_before_decision = []

    state: dict = {}
    for i, env in enumerate(sc.envelopes):
        last = i == len(sc.envelopes) - 1
        history_before = len(repo.history)
        action = sc.decide if last else "approve"

        def decide(requests, _before=history_before, _action=action):
            # HITL check: nothing may have been written before the user's answer.
            written_before_decision.append(len(repo.history) != _before)
            if _action is None:
                return None
            return [{"request_id": r["id"], "action": _action} for r in requests]

        state = run_without_graph(env, deps, decide=decide if action else None,
                                  observe=recorder.observe)
        if action is None and state.get("requests"):
            written_before_decision.append(len(repo.history) != history_before)

    skipped = {s.split(":")[0] for s in state.get("summary", {}).get("skipped", [])}
    predicted = {normalize_name(t["name"]) for t in state.get("plan", {}).get("topics", [])
                 if t["name"] not in skipped} if state.get("status") == "applied" else set()
    outcome = {
        "id": sc.id, "area": sc.area,
        "status": state.get("status"),
        "requests": sorted(r["kind"] for r in state.get("requests", [])),
        "applied_marked": repo.already_applied(sc.envelopes[-1]["idempotency_key"]),
        "evidence": len(repo.evidence),
        "predicted_topics": predicted,
        "writes_before_decision": any(written_before_decision),
    }
    failures = []
    if outcome["status"] != sc.expect_status:
        failures.append("status")
    if outcome["requests"] != sorted(sc.expect_requests):
        failures.append("requests")
    if outcome["applied_marked"] != sc.expect_applied_marked:
        failures.append("applied_marked")
    if sc.expect_evidence is not None and outcome["evidence"] != sc.expect_evidence:
        failures.append("evidence")
    if outcome["writes_before_decision"]:
        failures.append("writes_before_decision")
    outcome["failures"] = failures
    # Content that must never appear in monitoring data: all chunk texts and file paths
    # of this scenario, plus scenario-specific strings (names, secrets, injections).
    forbidden = list(sc.forbidden_in_telemetry)
    for env in sc.envelopes:
        forbidden += [c["text"] for c in env["chunks"]] + [env["file"]["rel_path"]]
    outcome["forbidden"] = forbidden
    return outcome


def evaluate(run_id: str = "eval", mastery_model=None) -> dict:
    recorder = MetadataRecorder(run_id)
    outcomes = [run_scenario(sc, recorder) for sc in scenarios()]
    truth = {sc.id: sc.truth_topics for sc in scenarios() if sc.truth_topics is not None}

    tp = fp = fn = 0
    for o in outcomes:
        if o["id"] in truth:
            expected = {normalize_name(t) for t in truth[o["id"]]}
            tp += len(expected & o["predicted_topics"])
            fp += len(o["predicted_topics"] - expected)
            fn += len(expected - o["predicted_topics"])
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    telemetry = recorder.export()
    leaks = find_leaks(telemetry, [f for o in outcomes for f in o["forbidden"]])
    hitl_ok = sum(1 for o in outcomes if "requests" not in o["failures"])
    return {
        "run_id": recorder.run_id,
        "metrics": {
            "classification_precision": round(precision, 4),
            "classification_recall": round(recall, 4),
            "classification_f1": round(f1, 4),
            "hitl_accuracy": round(hitl_ok / len(outcomes), 4),
            "writes_before_decision": sum(o["writes_before_decision"] for o in outcomes),
            "privacy_leaks": len(leaks),
            "reliability": round(sum(not o["failures"] for o in outcomes) / len(outcomes), 4),
            "mastery_quality": mastery_quality(mastery_model or model_from_settings(None))[0],
            "recommendation_quality": recommendation_quality()[0],
        },
        # ids and failure labels only – no content
        "scenarios": [{"id": o["id"], "area": o["area"], "failures": o["failures"]}
                      for o in outcomes],
        "telemetry_records": len(telemetry),
        "telemetry": telemetry,
    }
