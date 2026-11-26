"""Understanding assessment (spec §5, §14, §50).

APPROVED 2026-11-24: the Bayesian formula (claude/mastery-formula-proposal.md) –
`BayesianMasteryModel`, all numbers in settings.MasterySettings. Also in this module:
- `MasteryModel` – the interface any formula must implement;
- `PlaceholderMasteryModel` – never changes anything;
- `ProvisionalMasteryModel` – a clearly marked TEST model (all numbers in
  settings.MasterySettings, status ASSUMPTION) so the full learning cycle can run;
- `understood_guard` – rules that come from APPROVED principles and apply to ANY formula:
  no "understood" from a single result, several kinds of evidence, at least one success
  without heavy help, AI-generated work never counts;
- `reassess` – runs a model and writes the change, with its reason, to the history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from brain.domain.models import Evidence, Topic
from brain.i18n import msg, render
from brain.settings import MasterySettings


class MasteryModel(Protocol):
    name: str

    def update(self, topic: Topic, evidence: list[Evidence], now: str) -> tuple | None:
        """All evidence of the topic -> (state, score, reason, confidence) if the assessment
        changes, else None."""
        ...


class PlaceholderMasteryModel:
    name = "placeholder"

    def update(self, topic, evidence, now):
        return None


def _days(earlier: str, later: str) -> float:
    try:
        return max(0.0, (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() / 86400)
    except ValueError:
        return 0.0


def understood_guard(graded: list[Evidence], success: float) -> tuple[bool, str]:
    """APPROVED principles (spec §5, §12 item 12, §7, §14) – independent of the formula."""
    if len(graded) < 2:
        return False, "guard.single"
    if len({e.type for e in graded}) < 2:
        return False, "guard.kinds"
    if not any(e.independence in (None, "independent") and (e.score or 0) >= success for e in graded):
        return False, "guard.independent"
    return True, "m.understood"


def evidence_weight(e: Evidence, s: MasterySettings, now: str) -> float:
    """weight = type weight x independence factor x age decay (APPROVED)."""
    return (s.type_weights.get(e.type, 1.0) * s.independence_factor.get(e.independence, 1.0)
            * 0.5 ** (_days(e.occurred_at or now, now) / s.half_life_days))


class BayesianMasteryModel:
    """APPROVED formula. Start from 50/50 (prior points for and against); every graded result
    adds weight x score "for" and weight x (1 - score) "against":
        score = (prior + for) / (2 * prior + for + against)
    confidence = total weight. Old evidence fades, so with time the score drifts back toward
    "not sure" – never toward zero."""

    name = "bayesian"

    def __init__(self, settings: MasterySettings | None = None):
        self.s = settings or MasterySettings()

    def update(self, topic, evidence, now):
        s = self.s
        own = [e for e in evidence if e.counts_as_user_ability]
        graded = [e for e in own if e.type != "exposure" and e.score is not None]
        if not graded:
            if topic.state == "new" and any(e.type == "exposure" for e in evidence):
                return ("learning", topic.score, msg("m.exposure"), topic.confidence)
            return None
        pro = con = 0.0
        for e in graded:
            w = evidence_weight(e, s, now)
            pro += w * e.score
            con += w * (1 - e.score)
        score = round((s.prior + pro) / (2 * s.prior + pro + con), 3)
        confidence = round(pro + con, 2)
        allowed, guard_reason = understood_guard(graded, s.understood_score)
        if topic.state == "understood":
            # hysteresis: one weaker result does not undo a proven understanding
            if score >= s.keep_understood_score:
                state, why = "understood", "m.understood"
            else:
                state, why = "reinforce", "m.gap"
        elif score >= s.understood_score and allowed:
            state, why = "understood", guard_reason
        elif score < s.reinforce_score and confidence >= s.reinforce_min_weight:
            state, why = "reinforce", "m.gap"
        else:
            state, why = "learning", (guard_reason if score >= s.understood_score else "m.progress")
        if (state, score, confidence) == (topic.state, topic.score, topic.confidence):
            return None
        kinds = [msg("ev." + k) for k in sorted({e.type for e in graded})]
        return state, score, msg("m.reason", model_key="model.bayesian", score=f"{score:.2f}",
                                 n=len(graded), kinds=kinds, why_key=why), confidence


class ProvisionalMasteryModel:
    """SUPERSEDED test model (weighted average) – kept ONLY for before/after comparison with
    the approved formula. Not used by default."""

    name = "provisional"
    UNDERSTOOD, REINFORCE = 0.8, 0.5        # its own old values

    def __init__(self, settings: MasterySettings | None = None):
        self.s = settings or MasterySettings()

    def update(self, topic, evidence, now):
        own = [e for e in evidence if e.counts_as_user_ability]
        graded = [e for e in own if e.type != "exposure" and e.score is not None]
        if not graded:
            # Exposure = the topic appears in study material (any author). AI-generated
            # graded work is NOT exposure and never moves the state.
            if topic.state == "new" and any(e.type == "exposure" for e in evidence):
                return ("learning", topic.score, msg("m.exposure"), topic.confidence)
            return None
        total = weighted = 0.0
        for e in graded:
            w = (self.s.type_weights.get(e.type, 1.0) * self.s.independence_factor.get(e.independence, 1.0)
                 * 0.5 ** (_days(e.occurred_at or now, now) / self.s.half_life_days))
            total += w
            weighted += w * e.score
        score = round(weighted / total, 3) if total else 0.0
        newest = min(_days(e.occurred_at or now, now) for e in graded)
        allowed, guard_reason = understood_guard(graded, self.UNDERSTOOD)
        if topic.state == "understood" and newest > self.s.retention_days:
            state, why = "reinforce", msg("m.retention", days=f"{newest:.0f}")
        elif score >= self.UNDERSTOOD and allowed:
            state, why = "understood", guard_reason
        elif score < self.REINFORCE:
            state, why = "reinforce", "m.gap"
        else:
            state, why = "learning", (guard_reason if score >= self.UNDERSTOOD else "m.progress")
        if state == topic.state and score == topic.score:
            return None
        kinds = [msg("ev." + k) for k in sorted({e.type for e in graded})]
        return state, score, msg("m.reason", model_key="model.provisional", score=f"{score:.2f}",
                                 n=len(graded), kinds=kinds, why_key=why), round(total, 2)


def model_from_settings(settings: MasterySettings | None) -> MasteryModel:
    settings = settings or MasterySettings()
    return {"bayesian": BayesianMasteryModel, "provisional": ProvisionalMasteryModel}.get(
        settings.model, lambda _s: PlaceholderMasteryModel())(settings)


def _historical_contexts(repo) -> set:
    return {c.id for c in getattr(repo, "study_contexts", {}).values()
            if c.study_status in ("completed", "not_studied")}


def reassess(repo, model: MasteryModel, topic: Topic, cause, evidence_ids: list[str],
             lang: str | None = None) -> dict | None:
    """Run the model on ALL evidence of the topic; record any change with its reason.
    cause / reason are message keys or messages (brain.i18n), rendered only for display."""
    evidence = [e for e in repo.evidence.values() if e.topic_id == topic.id]
    # Decision 2026-11-24 (A7, APPROVED): material she FINISHED or has NOT STUDIED yet does not move a
    # topic to 'learning'. Only exposures from active (or still unknown) material count for that rule.
    # Graded evidence and the approved formula are untouched.
    historical = _historical_contexts(repo)
    evidence = [e for e in evidence if not (e.type == "exposure" and e.context_id in historical)]
    change = model.update(topic, evidence, repo.clock())
    if change is None and topic.state == "learning" and not any(
            e.counts_as_user_ability and e.type != "exposure" and e.score is not None for e in evidence) \
            and not any(e.type == "exposure" for e in evidence):
        # 'learning' came only from material that is now classified finished / not studied
        change = ("new", topic.score, msg("m.historical_only"), topic.confidence)
    if change is None:
        return None
    before = {"state": topic.state, "score": topic.score, "confidence": topic.confidence}
    topic.state, topic.score, reason, topic.confidence = change
    after = {"state": topic.state, "score": topic.score, "confidence": topic.confidence, "model": model.name}
    message = msg("mastery.updated", cause_key=cause, detail_key=reason)
    repo.record("topic", topic.id, "mastery_updated", before, after, message,
                "autonomous", evidence_ids=evidence_ids)   # update_mastery = APPROVED autonomous
    return {"topic_id": topic.id, "before": before, "after": after, "reason": render(reason, lang),
            "reason_msg": reason}
