"""User decisions about files held for review.

A decision is tied to the exact file CONTENT (sha256): if the file changes,
it must be reviewed again.
  approve -> the uncertain items are fine; definite secrets are still removed
  ignore  -> this version of the file is never sent
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from collector.config import LOCAL_DATA

DEFAULT_REVIEW_PATH = LOCAL_DATA / "review.json"
APPROVE = "approve"
IGNORE = "ignore"


def load_decisions(path: Path = DEFAULT_REVIEW_PATH) -> dict[str, str]:
    """{sha256: decision}. Damaged file -> no decisions (files simply stay held)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if v in (APPROVE, IGNORE)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_decision(sha256: str, decision: str, path: Path = DEFAULT_REVIEW_PATH) -> None:
    decisions = load_decisions(path)
    decisions[sha256] = decision
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---- Manual content for files that could not be read (spec §5) ----
DEFAULT_MANUAL_PATH = LOCAL_DATA / "manual.json"


def load_manual(path: Path = DEFAULT_MANUAL_PATH) -> dict[str, str]:
    """{sha256: text typed by the user}. Damaged file -> nothing."""
    from collector.security import at_rest
    try:
        data = at_rest.read(path)                       # typed study text: encrypted at rest
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except (OSError, ValueError, AttributeError, RuntimeError):
        return {}


def save_manual(sha256: str, text: str, path: Path = DEFAULT_MANUAL_PATH) -> None:
    manual = load_manual(path)
    manual[sha256] = text
    from collector.security import at_rest
    at_rest.write(path, manual)
