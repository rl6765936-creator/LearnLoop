"""Knowledge-graph entities (spec Part IV, APPROVED entity / relation / state names).

Plain data classes: no database, no framework. Storage lives behind repository.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# APPROVED (spec §13)
TOPIC_STATES = ("new", "learning", "understood", "reinforce")
EDGE_KINDS = ("prerequisite", "related", "partOf")
# APPROVED evidence types (spec §14) + "exposure" [RECOMMENDATION]: the topic APPEARS in the
# user's study material. It is recorded so the system can tell exposure apart from real
# understanding; it never counts as understanding by itself.
EVIDENCE_TYPES = ("quiz", "explain", "apply", "transfer", "retention", "debug", "analyze",
                  "exposure")
AUTHORSHIP = ("user", "ai", "user_modified", "unknown")
INDEPENDENCE = ("independent", "hint", "substantial_help", "saw_solution")

# User decision 2026-11-24 [APPROVED]: three SEPARATE dimensions – never mixed.
#   1. Study Status     – what the material is for the user; EXPLICIT (her classification)
#   2. Assessment Status – whether her knowledge was actually assessed (derived from evidence)
#   3. Mastery Status   – the four approved TOPIC_STATES above (unchanged)
# Missing evidence is never shown as proof that she did not study something.
STUDY_STATUSES = ("completed", "active", "not_studied", "unknown")
ASSESSMENT_STATUSES = ("assessed", "not_assessed")


def normalize_name(name: str) -> str:
    """Case- and space-insensitive key for matching names ('Data  Structures' == 'data structures')."""
    return " ".join(name.casefold().split())


@dataclass
class Subject:
    id: str
    name: str
    approval_id: str | None      # spec §16: a new subject exists only after approval


@dataclass
class Topic:
    id: str
    subject_id: str
    name: str
    state: str = "new"
    score: float | None = None        # approved Bayesian formula (0..1); None = no graded evidence yet
    confidence: float | None = None   # total evidence weight behind the score (shown to the user)


@dataclass
class Skill:
    id: str
    topic_id: str
    name: str


@dataclass(frozen=True)
class Edge:
    src: str                     # topic / skill id
    dst: str
    kind: str                    # prerequisite: src is needed before dst


@dataclass
class ContentItem:
    source_id: str
    rel_path: str
    sha256: str
    status: str = "active"       # active | source_removed (spec §4: stop using, keep history)


@dataclass
class Chunk:
    chunk_id: str
    content_sha: str
    location: str                # exact segment for traceability (APPROVED §4)
    text: str
    status: str = "active"


@dataclass
class Evidence:
    id: str
    topic_id: str
    type: str
    authorship: str
    chunk_id: str
    # Spec §7 (APPROVED): AI-generated code is never proof of the user's ability.
    counts_as_user_ability: bool
    note: str = ""
    # spec §14 (APPROVED): independence vs. assistance. None = not applicable (file material)
    independence: str | None = None      # independent | hint | substantial_help | saw_solution
    score: float | None = None           # 0..1 result of an assessment; None for exposure
    occurred_at: str = ""
    source: str = "file"                 # file | activity
    activity_id: str | None = None
    # Which study context (source + folder + subject + period) the evidence came from.
    # Keeps the origin of every piece of evidence (decision 2026-11-24).
    context_id: str | None = None
    skill_id: str | None = None          # the skill this evidence is about, when known


@dataclass
class Skill:
    """spec §13 [APPROVED entity]: a specific ability that can be assessed, part of a topic
    (edge partOf: skill -> topic). A separate MASTERY SCORE per skill is still OPEN (spec §50)."""
    id: str
    topic_id: str
    name: str


@dataclass
class StudyContext:
    """One study period of one subject, as it appears in one collection (folder) of a source.
    The same subject can have several (e.g. C# in 'שנה א'' = completed, C# now = active), so
    there is NO single global study status per subject."""
    id: str
    source_id: str
    collection: str              # folder inside the approved folder ("" = its main folder)
    subject_id: str
    study_status: str = "unknown"
    period: str | None = None
    decided_by: str = "none"     # collector_setting | user_decision | none


@dataclass
class ChangeRecord:
    """One line of the append-only history (spec §13 'History', APPROVED)."""
    seq: int
    at: str
    entity: str                  # subject | topic | edge | evidence | content | approval
    entity_id: str
    action: str                  # created | updated | source_removed | decided ...
    before: dict | None
    after: dict | None
    reason: object               # message dict (brain.i18n.msg) or plain text (older records)
    mode: str                    # autonomous | approved | user
    approval_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
