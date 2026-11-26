"""What the Collector remembers between runs about each file.

Stored in local_data/state.json. It holds file names, sizes, dates and
content fingerprints (SHA-256) – NOT the extracted text itself.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from collector.config import LOCAL_DATA

DEFAULT_STATE_PATH = LOCAL_DATA / "state.json"
REMOVED = "removed"


@dataclass
class FileRecord:
    sha256: str          # fingerprint of the content: same content -> same value
    size: int
    mtime_ns: int        # last-modified time (nanoseconds)
    status: str          # extraction status (ok / failed / ...) or "removed"
    error: str | None = None
    segments: int = 0
    attempts: int = 0    # failed read attempts for this content


@dataclass
class MonitorState:
    root: str | None = None
    grant_id: str | None = None   # approval identity this state was built under
    files: dict[str, FileRecord] = field(default_factory=dict)  # key: path relative to root


def load_state(path: Path = DEFAULT_STATE_PATH) -> tuple[MonitorState, str | None]:
    """Never raises. A damaged state file means: start fresh (everything is re-read)."""
    if not path.exists():
        return MonitorState(), None
    try:
        from collector.security import at_rest   # §57: the file list is encrypted at rest (DPAPI)
        data = at_rest.read(path)
        files = {rel: FileRecord(**rec) for rel, rec in data["files"].items()}
        return MonitorState(root=data.get("root"), grant_id=data.get("grant_id"), files=files), None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as e:
        return MonitorState(), f"state file could not be used ({type(e).__name__}) – starting fresh"


def save_state(state: MonitorState, path: Path = DEFAULT_STATE_PATH) -> None:
    """Atomic save: temporary file, then replace (same technique as config.py)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"root": state.root, "grant_id": state.grant_id, "files": {rel: asdict(r) for rel, r in state.files.items()}}
    from collector.security import at_rest     # file names of her study material: encrypted at rest
    at_rest.write(path, data)
