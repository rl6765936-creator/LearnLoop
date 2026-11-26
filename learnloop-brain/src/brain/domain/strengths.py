"""Personal strengths – evidence-based, never automatic compliments (spec §42, APPROVED).

The spec asks for: demonstrated successes, thinking abilities, persistence and progress,
subject-specific strengths, and general abilities that recur across subjects – and to use
them to suggest suitable reinforcement.

Rules (thresholds are settings, ASSUMPTION – the spec gives no numbers):
  success     a topic in state 'understood' (the approved formula already required her own,
              varied, independent evidence) – listed per subject.
  ability     one way of thinking (explain / apply / transfer / debug / analyze) where HER answers
              are strong: at least `min_answers` answers, average ≥ `min_average`, solved
              independently or with one hint. Per subject; GENERAL when strong in ≥ 2 subjects.
  progress    a topic that went from 'reinforce' to 'understood', or whose graded result rose
              by ≥ `min_progress` from her first to her latest answers – persistence that paid off.
Only evidence that counts as her ability is used (never exposure, never AI-generated work).
Every item carries the numbers it is based on, so it can be checked.
"""

from __future__ import annotations

from collections import defaultdict

from brain.i18n import t

ABILITY_TYPES = ("explain", "apply", "transfer", "debug", "analyze")
INDEPENDENT = ("independent", "hint")


def _her_evidence(repo):
    return [e for e in repo.evidence.values()
            if e.counts_as_user_ability and e.authorship != "ai" and e.type != "exposure" and e.score is not None]


def abilities(repo, settings, subject_id: str | None = None) -> dict[str, dict]:
    """{evidence_type: {"subjects": {subject_id: (n, avg)}}} for strong abilities."""
    s = settings.strengths
    per: dict[tuple[str, str], list[float]] = defaultdict(list)
    for e in _her_evidence(repo):
        topic = repo.topics.get(e.topic_id)
        if topic is None or e.type not in ABILITY_TYPES or e.independence not in INDEPENDENT:
            continue
        per[(e.type, topic.subject_id)].append(e.score)
    strong: dict[str, dict] = {}
    for (etype, sid), scores in per.items():
        avg = sum(scores) / len(scores)
        if len(scores) >= s.min_answers and avg >= s.min_average:
            strong.setdefault(etype, {"subjects": {}})["subjects"][sid] = (len(scores), round(avg, 2))
    if subject_id is not None:
        strong = {k: v for k, v in strong.items() if subject_id in v["subjects"]}
    return strong


def strengths(repo, settings, subject_id: str | None = None, lang: str = "he") -> list[dict]:
    s = settings.strengths
    out: list[dict] = []
    subjects = [x for x in repo.subjects.values() if subject_id is None or x.id == subject_id]

    for subj in subjects:                                             # demonstrated successes
        done = sorted(tp.name for tp in repo.topics.values() if tp.subject_id == subj.id and tp.state == "understood")
        if done:
            out.append({"kind": "success", "subject_id": subj.id, "count": len(done),
                        "text": t("str.success", lang, n=len(done), subject=subj.name, topics=", ".join(done[:5]))})

    for etype, info in abilities(repo, settings, subject_id).items():   # thinking abilities
        label = t("ev." + etype, lang)
        if subject_id is None and len(info["subjects"]) >= 2:
            n = sum(c for c, _a in info["subjects"].values())
            out.append({"kind": "general_ability", "evidence_type": etype, "count": n,
                        "subjects": list(info["subjects"]),
                        "text": t("str.general", lang, ability=label, k=len(info["subjects"]), n=n)})
            continue
        for sid, (n, avg) in info["subjects"].items():
            if sid in repo.subjects:
                out.append({"kind": "ability", "evidence_type": etype, "subject_id": sid, "count": n, "average": avg,
                            "text": t("str.ability", lang, ability=label, subject=repo.subjects[sid].name,
                                      n=n, avg=int(avg * 100))})

    graded: dict[str, list] = defaultdict(list)                       # persistence and progress
    for e in sorted(_her_evidence(repo), key=lambda e: e.occurred_at or ""):
        graded[e.topic_id].append(e.score)
    for topic in repo.topics.values():
        if subject_id is not None and topic.subject_id != subject_id:
            continue
        states = [r.after.get("state") for r in repo.history
                  if r.entity == "topic" and r.entity_id == topic.id and isinstance(r.after, dict)]
        came_back = "reinforce" in states and topic.state == "understood"
        scores = graded.get(topic.id, [])
        k = max(1, len(scores) // 3)
        rise = (sum(scores[-k:]) / k - sum(scores[:k]) / k) if len(scores) >= 2 * k and len(scores) >= 2 else 0
        if came_back or rise >= s.min_progress:
            out.append({"kind": "progress", "subject_id": topic.subject_id, "topic_id": topic.id,
                        "text": t("str.came_back" if came_back else "str.progress", lang, topic=topic.name,
                                  rise=int(round(rise * 100)))})
    return out


def best_ability(repo, settings, subject_id: str) -> str | None:
    """The strongest ability in this subject – used to suggest reinforcement (spec §42)."""
    strong = abilities(repo, settings, subject_id)
    if not strong:
        return None
    return max(strong, key=lambda k: strong[k]["subjects"][subject_id][1])
