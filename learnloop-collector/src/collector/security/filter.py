"""Apply the detectors to an ExtractionResult.

Outcome:
  clean          -> nothing found, content may continue
  redacted       -> definite findings removed, content may continue
  pending_review -> something uncertain: NOTHING from this file continues until the user decides
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from collector.readers.base import ExtractionResult, Segment
from collector.security.detectors import DEFINITE, UNCERTAIN, Finding, find_sensitive

CLEAN = "clean"
REDACTED = "redacted"
PENDING_REVIEW = "pending_review"


@dataclass
class Redaction:
    kind: str
    location: str   # where in the file (never the secret itself)


@dataclass
class FilterOutcome:
    status: str
    segments: list[Segment] = field(default_factory=list)
    redactions: list[Redaction] = field(default_factory=list)
    uncertain: list[Redaction] = field(default_factory=list)


def normalize(text: str) -> str:
    """Remove invisible format characters and unify look-alike characters.

    Otherwise 'pass\\u200bword=...' (zero-width space inside) or full-width letters
    could hide a secret from the detectors. NFKC also unifies such look-alikes.
    """
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _redact(text: str, findings: list[Finding]) -> tuple[str, list[str]]:
    """Replace definite findings from the end backwards, so positions stay valid.
    Overlapping findings (two detectors, same secret) are merged into one.
    Returns the new text and the kinds actually removed."""
    spans = sorted((f.start, f.end, f.kind) for f in findings if f.certainty == DEFINITE)
    merged: list[list] = []
    for start, end, kind in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, kind])
    for start, end, kind in reversed(merged):
        text = text[:start] + f"[REDACTED:{kind}]" + text[end:]
    return text, [kind for _, _, kind in merged]


def filter_result(result: ExtractionResult, accept_uncertain: bool = False) -> FilterOutcome:
    """accept_uncertain=True only after the user reviewed and approved this exact file version."""
    safe_segments, redactions, uncertain = [], [], []
    for segment in result.segments:
        text = normalize(segment.text)
        findings = find_sensitive(text)
        uncertain += [Redaction(f.kind, segment.location) for f in findings if f.certainty == UNCERTAIN]
        safe_text, removed = _redact(text, findings)
        redactions += [Redaction(kind, segment.location) for kind in removed]
        safe_segments.append(Segment(segment.location, safe_text))

    if uncertain and not accept_uncertain:
        return FilterOutcome(PENDING_REVIEW, [], redactions, uncertain)  # fail closed
    status = REDACTED if redactions else CLEAN
    return FilterOutcome(status, safe_segments, redactions, uncertain)
