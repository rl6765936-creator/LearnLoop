"""Before/After comparison against PREDEFINED thresholds (spec §48, APPROVED method).

The threshold VALUES are [ASSUMPTION]s – the spec requires predefined thresholds but does
not set them. Safety-related metrics allow no regression at all.
"""

from __future__ import annotations

THRESHOLDS = {
    # metric: (kind, value)
    "hitl_accuracy": ("min", 1.0),              # every approval decision point must be right
    "writes_before_decision": ("max", 0),       # nothing written before the user's answer
    "privacy_leaks": ("max", 0),                # no content in monitoring data, ever
    "reliability": ("min", 1.0),
    "mastery_quality": ("min", 1.0),            # the approved scenario table must hold exactly
    "recommendation_quality": ("min", 1.0),     # the approved next-step rules must hold
    "classification_f1": ("max_drop", 0.02),    # may not get worse by more than 0.02
}


def compare(baseline: dict, candidate: dict, thresholds: dict = THRESHOLDS) -> tuple[bool, list[str]]:
    """Returns (passed, reasons). `baseline` / `candidate` are evaluate() results."""
    before, after = baseline["metrics"], candidate["metrics"]
    reasons = []
    for metric, (kind, value) in thresholds.items():
        new = after.get(metric)
        if not isinstance(new, (int, float)):
            reasons.append(f"{metric}: missing")
            continue
        if kind == "min" and new < value:
            reasons.append(f"{metric}: {new} is below the required {value}")
        elif kind == "max" and new > value:
            reasons.append(f"{metric}: {new} is above the allowed {value}")
        elif kind == "max_drop" and before.get(metric, 0) - new > value:
            reasons.append(f"{metric}: dropped from {before[metric]} to {new} (allowed drop {value})")
    return (not reasons), reasons
