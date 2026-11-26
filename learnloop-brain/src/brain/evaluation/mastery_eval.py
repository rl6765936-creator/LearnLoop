"""Mastery quality on SYNTHETIC scenarios: the approved table (mastery-formula-proposal §5).
Expected states are the ones the user approved; a model scores the share it gets right."""

from __future__ import annotations

from brain.domain.mastery import reassess
from brain.domain.models import Evidence
from brain.domain.repository import InMemoryRepository

NOW = "2026-11-24T12:00:00+00:00"
OLD = "2026-09-25T12:00:00+00:00"
I = "independent"

# (id, previous state, [(type, score, independence, when)], expected state)
SCENARIOS = [
    ("single_perfect_quiz", "learning", [("quiz", 1.0, I, NOW)], "learning"),
    ("quiz_and_explain", "learning", [("quiz", 1.0, I, NOW), ("explain", 0.9, I, NOW)], "understood"),
    ("with_help", "learning", [("quiz", 1.0, "hint", NOW), ("explain", 0.9, "saw_solution", NOW)], "learning"),
    ("three_varied", "learning", [("quiz", 1.0, I, NOW), ("explain", 0.9, I, NOW), ("debug", 0.8, I, NOW)], "understood"),
    ("two_weak", "learning", [("quiz", 0.2, I, NOW), ("explain", 0.3, I, NOW)], "reinforce"),
    ("time_alone", "understood", [("quiz", 1.0, I, OLD), ("explain", 0.9, I, OLD), ("debug", 0.8, I, OLD)], "understood"),
    ("failed_retention", "understood", [("quiz", 1.0, I, OLD), ("explain", 0.9, I, OLD), ("debug", 0.8, I, OLD),
                                         ("retention", 0.2, I, NOW)], "reinforce"),
    ("five_same_kind", "learning", [("quiz", 1.0, I, NOW)] * 5, "learning"),
]


def mastery_quality(model) -> tuple[float, list[str]]:
    wrong = []
    for sid, previous, results, expected in SCENARIOS:
        repo = InMemoryRepository(clock=lambda: NOW)
        subject = repo.add_subject("S", "r", "t")
        topic = repo.add_topic(subject.id, "T", "t", "autonomous")
        topic.state = previous
        for type_, score, ind, when in results:
            repo.add_evidence(Evidence(repo.new_id("ev"), topic.id, type_, "user", "c", True, score=score,
                                       independence=ind, occurred_at=when, source="activity"), "t", "autonomous")
        reassess(repo, model, topic, "t", [])
        if topic.state != expected:
            wrong.append(sid)
    return round(1 - len(wrong) / len(SCENARIOS), 4), wrong
