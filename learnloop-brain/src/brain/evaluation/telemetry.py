"""Metadata-only monitoring records (spec §12, §49 – APPROVED: metadata only by default).

Allow-list design: a record can contain ONLY the fields below, and text fields can only
take values from fixed vocabularies. Free text (file names, subject names, chunk text,
prompts, model output, raw error messages) has no field to go into, so it cannot leak by
accident. Error messages are reduced to a fixed category.
"""

from __future__ import annotations

STEPS = {"validate", "analyze", "plan", "policy_gate", "apply_changes", "apply_removal"}
STATUSES = {"received", "rejected", "duplicate", "removal", "failed", "routine",
            "waiting_for_user", "applied", None}
REQUEST_KINDS = {"new_subject", "low_confidence", "conflict", "major_graph", "sensitive_transfer"}
ERROR_CATEGORIES = ("contract", "permission", "model", "other")


def error_category(message: str | None) -> str | None:
    if not message:
        return None
    if "not approved for transfer" in message:
        return "permission"
    if "invalid at" in message or "rejected" in message:
        return "contract"
    if "model call failed" in message:
        return "model"
    return "other"


class MetadataRecorder:
    def __init__(self, run_id: str):
        self.run_id = run_id if run_id.replace("-", "").replace("_", "").isalnum() else "run"
        self.records: list[dict] = []
        self.scenario = "unknown"

    def set_scenario(self, scenario_id: str) -> None:
        # scenario ids are synthetic, fixed identifiers (checked: letters, digits, _ only)
        self.scenario = scenario_id if scenario_id.replace("_", "").isalnum() else "unknown"

    def observe(self, step: str, seconds: float, state: dict) -> None:
        status = state.get("status")
        kinds = sorted({r.get("kind") for r in state.get("requests", [])} & REQUEST_KINDS)
        self.records.append({
            "run_id": self.run_id,
            "scenario": self.scenario,
            "step": step if step in STEPS else "other",
            "seconds": round(float(seconds), 4),
            "status": status if status in STATUSES else "other",
            "error_category": error_category(state.get("error")),
            "request_kinds": kinds,
            "model_attempts": int(state.get("attempts", 0)),
        })

    def export(self) -> list[dict]:
        return [dict(r) for r in self.records]


def find_leaks(records: list[dict], forbidden: list[str]) -> list[str]:
    """Which forbidden strings (content, names, secrets) appear anywhere in the records."""
    blob = repr(records)
    return sorted({f for f in forbidden if f and f in blob})
