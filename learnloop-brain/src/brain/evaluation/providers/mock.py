"""Mock mode for the provider evaluation: an offline "oracle" that answers the Hebrew
scenarios correctly, in each provider's own wire format (via the real adapters).

What mock mode proves: the evaluation pipeline, adapters, schema transforms, contract
validation, metrics and report all work end to end without network.
What it does NOT prove: anything about a real provider's quality, latency or cost.
The report says so in its first line.
"""

from __future__ import annotations

from brain.evaluation.providers import scenarios_he as S
from brain.model_gateway import fake_activity


def oracle(task: str, payload: dict) -> dict:
    if task == "analyze":
        text = payload["chunks"][0]["text"]
        sc = next(s for s in S.ANALYSIS if s.chunks[0] == text)
        topics = [{"name": al[0], "confidence": 0.85, "chunk_ids": ["c0"], "matched_topic_id": None,
                   "prerequisites": []} for al in sc.topics.values()]
        etype, author = ("explain", "user") if sc.own_work else ("exposure", "unknown")
        return {"subject": {"name": sc.subject[0], "matched_subject_id": sc.expect_match, "confidence": 0.9},
                "topics": topics,
                "evidence": [{"topic": t["name"], "type": etype, "authorship": author, "chunk_id": "c0"}
                             for t in topics]}
    if task == "generate_activity":
        return fake_activity(payload["spec"])
    sc = next(s for s in S.GRADING if s.answer == payload["answer"])
    lo, hi = sc.band
    good = lo >= 0.7
    return {"score": round((lo + hi) / 2, 2),
            "feedback": "יפה – הרעיון המרכזי נמצא בתשובה." if good else "כדאי לחזור להגדרה: תאוצה היא קצב שינוי המהירות.",
            "mistake_type": "none" if good else "misconception"}
