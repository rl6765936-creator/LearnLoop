"""All tunable values in one place, each with its decision status.

The user's rule (2026-11-24): assumptions must NOT silently become product decisions.
So every value here is either APPROVED (copied from the Master Spec, not meant to be
tuned) or an ASSUMPTION / RECOMMENDATION default that can be changed without touching code.
`describe()` lists them for the documentation and the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


def _s(default, status: str, why: str):
    return field(default=default, metadata={"status": status, "why": why})


@dataclass
class PolicySettings:
    low_confidence: float = _s(0.6, "ASSUMPTION", "below this the user is asked (spec: low_confidence, no number)")
    major_new_topics: int = _s(5, "ASSUMPTION", "more new topics in one batch = major_graph")
    autonomous_new_topics: bool = _s(True, "ASSUMPTION – to confirm",
                                     "new topic under an EXISTING subject treated as classify")
    analysis_window_chars: int = _s(90_000, "ASSUMPTION",
                                    "spec §35: a very large file is analysed in parts of at most this many characters, "
                                    "each part told what earlier parts contained")
    autonomous_prerequisites: bool = _s(True, "ASSUMPTION – to confirm",
                                        "prerequisite edges proposed from material added without asking")


@dataclass
class MasterySettings:
    # Formula APPROVED by the user on 2026-11-24 (claude/mastery-formula-proposal.md, option B).
    # All values are settings: they can be changed without touching code.
    model: str = _s("bayesian", "APPROVED", "approved Bayesian formula; 'provisional' kept only for comparison")
    type_weights: dict = _s(None, "APPROVED", "weight of each evidence type (exposure = 0: never understanding)")
    independence_factor: dict = _s(None, "APPROVED", "help lowers the weight: hint 0.7, much help 0.4, solution seen 0.1")
    half_life_days: float = _s(30.0, "APPROVED", "a 30-day-old result counts half")
    prior: float = _s(1.0, "APPROVED", "start from 50/50: one point for and one against")
    understood_score: float = _s(0.70, "APPROVED", "score needed for 'understood' (plus the safety conditions)")
    reinforce_score: float = _s(0.45, "APPROVED", "below this -> 'reinforce'")
    keep_understood_score: float = _s(0.55, "APPROVED", "an understood topic stays understood until below this")
    reinforce_min_weight: float = _s(1.0, "ASSUMPTION", "'some evidence' before 'reinforce' can be decided")
    time_alone_lowers_state: bool = _s(False, "APPROVED", "time alone never lowers a state; only a failed retention check does")
    show_confidence: bool = _s(True, "APPROVED", "show how much evidence a score is based on")
    retention_days: int = _s(21, "ASSUMPTION", "after this without evidence a retention check is SUGGESTED (no state change)")

    def __post_init__(self):
        if self.type_weights is None:
            self.type_weights = {"quiz": 1.0, "explain": 1.5, "apply": 1.5, "transfer": 2.0,
                                 "retention": 1.5, "debug": 1.5, "analyze": 1.5, "exposure": 0.0}
        if self.independence_factor is None:
            self.independence_factor = {"independent": 1.0, "hint": 0.7, "substantial_help": 0.4,
                                        "saw_solution": 0.1, None: 1.0}


@dataclass
class ActivitySettings:
    unfinished_quiz_days: int = _s(2, "APPROVED", "spec §6: unfinished quiz accessible for two days, then red indicator")
    max_items: int = _s(5, "RECOMMENDATION", "spec: activities short and focused")
    refresh_days: int = _s(7, "APPROVED", "spec §21: weekly refresher for subjects already studied")
    placement_max_topics: int = _s(8, "ASSUMPTION", "broad initial quiz: at most this many topics at once")


@dataclass
class StrengthSettings:
    min_answers: int = _s(3, "ASSUMPTION", "spec §42: an ability counts as a strength after at least N of her answers")
    min_average: float = _s(0.8, "ASSUMPTION", "… with at least this average result")
    min_progress: float = _s(0.25, "ASSUMPTION", "progress = her results rose at least this much")


@dataclass
class ScheduleSettings:
    interval_hours: float = _s(2, "APPROVED", "spec §35: routine AI processing every two hours while the computer runs")
    accumulation_files: int = _s(20, "ASSUMPTION", "spec §35 early trigger 'significant accumulation' – files waiting")
    accumulation_chars: int = _s(300_000, "ASSUMPTION", "… or characters waiting")
    quiet_minutes: int = _s(20, "ASSUMPTION", "spec §35: while she studies (activity answered in the last N minutes) "
                                               "routine processing waits")


@dataclass
class Settings:
    language: str = _s("he", "RECOMMENDATION", "language of all user-facing texts (he / en)")
    policy: PolicySettings = field(default_factory=PolicySettings)
    mastery: MasterySettings = field(default_factory=MasterySettings)
    activities: ActivitySettings = field(default_factory=ActivitySettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    strengths: StrengthSettings = field(default_factory=StrengthSettings)


def describe(settings: Settings | None = None) -> list[dict]:
    settings = settings or Settings()
    rows = []
    for section in fields(settings):
        obj = getattr(settings, section.name)
        if not hasattr(obj, "__dataclass_fields__"):
            rows.append({"setting": section.name, "value": obj, "status": section.metadata.get("status"),
                         "why": section.metadata.get("why")})
            continue
        for f in fields(obj):
            rows.append({"setting": f"{section.name}.{f.name}", "value": getattr(obj, f.name),
                         "status": f.metadata.get("status"), "why": f.metadata.get("why")})
    return rows
