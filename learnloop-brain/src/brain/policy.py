"""Policy – deterministic rules that decide when the user must approve (spec §16).

The model never decides this. Approval kinds (APPROVED): new_subject, low_confidence,
conflict, major_graph, sensitive_transfer. The NUMBERS below are [ASSUMPTION]s – the
spec does not define thresholds [GAP] – and live in one place so they are easy to change.

sensitive_transfer is not raised here: on the brain side, content that is not allowed to be
transferred is REJECTED on arrival (see ingestion.validate), it is never processed.
"""

from __future__ import annotations

import hashlib

from brain.i18n import DEFAULT_LANG, msg, render

LOW_CONFIDENCE = 0.6        # [ASSUMPTION] below this, a proposal needs the user
MAJOR_NEW_TOPICS = 5        # [ASSUMPTION] more new topics than this in one batch = major_graph


def request_id(idempotency_key: str, kind: str, refs: list[str]) -> str:
    """Deterministic: re-running the step (e.g. after a resume) yields the SAME ids."""
    raw = "|".join([idempotency_key, kind, *sorted(refs)])
    return "req_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def approvals_needed(plan: dict, idempotency_key: str, settings=None) -> list[dict]:
    """plan -> list of approval requests (JSON-serializable dicts). Empty list = routine.
    `settings` (brain.settings.Settings) overrides the default thresholds."""
    low = settings.policy.low_confidence if settings else LOW_CONFIDENCE
    major = settings.policy.major_new_topics if settings else MAJOR_NEW_TOPICS
    autonomous_topics = settings.policy.autonomous_new_topics if settings else True
    lang = getattr(settings, "language", None) or DEFAULT_LANG
    requests: list[dict] = []

    def add(kind: str, summary: dict, refs: list[str]):
        # summary_msg is language-neutral; summary is rendered for convenience
        requests.append({"id": request_id(idempotency_key, kind, refs), "kind": kind,
                         "summary": render(summary, lang), "summary_msg": summary, "refs": refs,
                         "options": ["approve", "reject", "correct"]})

    subject = plan["subject"]
    if subject["existing_id"] is None:
        add("new_subject", msg("req.new_subject", name=subject["name"]), [subject["name"]])
    elif subject["confidence"] < low:
        add("low_confidence", msg("req.low_subject", name=subject["name"]),
            ["subject:" + subject["existing_id"]])

    unsure = [t["name"] for t in plan["topics"] if t["confidence"] < low]
    if unsure:  # ONE request for all unsure topics – fewer questions for the user
        add("low_confidence", msg("req.low_topics", names=unsure),
            ["topic:" + n for n in unsure])

    for t in plan["topics"]:
        if t.get("conflict"):
            add("conflict", msg("req.conflict", name=t["name"]), ["topic:" + t["name"]])

    new_confident = [t["name"] for t in plan["topics"]
                     if t["existing_id"] is None and t["confidence"] >= low
                     and not t.get("conflict")]
    # A brand-new subject is approved as a whole; under an EXISTING subject, new topics are
    # autonomous unless the setting says otherwise (ASSUMPTION – to confirm).
    needs_topic_approval = (not autonomous_topics and subject["existing_id"] is not None
                            and new_confident)
    if len(new_confident) > major or needs_topic_approval:
        add("major_graph", msg("req.major", n=len(new_confident)),
            ["new_topics"])
    return requests
