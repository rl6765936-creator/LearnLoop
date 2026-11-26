"""The outbox: what WOULD be sent to the AI. Only filtered content ever gets here.

One JSON file per source file. When a source file is removed or needs review,
its outbox entry is removed (spec: stop using its information).

Approval tracking (user decision 2026-11-24, Q3 option A):
- Every entry records WHICH approval it was collected under (folder + grant id).
- Content collected under an approval that was revoked or replaced is moved to an
  isolated "quarantine" folder, marked transfer_allowed = false, and kept locally.
  It is never used or transferred again. Nothing is deleted from the quarantine;
  long-term retention policy is still [OPEN].
- transfer_gate() is the ONE check a future sender must call for every entry,
  right before sending it. The outbox itself never sends anything.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from collector.config import LOCAL_DATA, CollectorConfig
from collector.security.filter import FilterOutcome

DEFAULT_OUTBOX = LOCAL_DATA / "outbox"


def quarantine_dir(outbox: Path) -> Path:
    """Isolated folder next to the outbox (outbox -> outbox_quarantine)."""
    return outbox.parent / (outbox.name + "_quarantine")


def _entry_path(outbox: Path, rel: str) -> Path:
    # File names in the outbox are derived from the source path, so odd characters
    # in source names (Hebrew, hidden marks) never become file names here.
    return outbox / (hashlib.sha256(rel.encode("utf-8")).hexdigest()[:20] + ".json")


def _write_json(path: Path, data: dict) -> None:
    """Atomic write, ENCRYPTED at rest (security/at_rest.py: Windows DPAPI)."""
    from collector.security import at_rest
    at_rest.write(path, data)


def _read_json(path: Path) -> dict:
    from collector.security import at_rest
    return at_rest.read(path)


def write_entry(outbox: Path, rel: str, sha256: str, outcome: FilterOutcome,
                grant: tuple[str, str | None], reader: str = "",
                warnings: list[str] | None = None) -> None:
    """grant = CollectorConfig.grant_key() of the approval this content was read under."""
    outbox.mkdir(parents=True, exist_ok=True)
    data = {
        "source": rel,
        "sha256": sha256,
        "grant": {"folder": grant[0], "grant_id": grant[1]},
        "reader": reader,
        "warnings": list(warnings or []),   # e.g. OCR language limits – technical facts only
        "filter_status": outcome.status,
        "redactions": [asdict(r) for r in outcome.redactions],
        "segments": [asdict(s) for s in outcome.segments],
    }
    _write_json(_entry_path(outbox, rel), data)


def remove_entry(outbox: Path, rel: str) -> None:
    try:
        _entry_path(outbox, rel).unlink()
    except FileNotFoundError:
        pass


def move_entry(outbox: Path, old_rel: str, new_rel: str) -> None:
    old = _entry_path(outbox, old_rel)
    if not old.exists():
        return
    data = _read_json(old)
    data["source"] = new_rel
    _write_json(_entry_path(outbox, new_rel), data)   # atomic, keeps the grant tag
    old.unlink()


def list_entries(outbox: Path) -> list[dict]:
    """All readable entries. A damaged entry file is skipped (it can never be transferred)."""
    if not outbox.exists():
        return []
    entries = []
    for p in sorted(outbox.glob("*.json")):
        try:
            entries.append(_read_json(p))
        except (OSError, ValueError, RuntimeError):
            continue
    return entries


def _entry_grant(entry: dict) -> tuple[str, str | None] | None:
    grant = entry.get("grant")
    if not isinstance(grant, dict) or not isinstance(grant.get("folder"), str):
        return None
    return (grant["folder"], grant.get("grant_id"))


def transfer_gate(entry: dict, config: CollectorConfig) -> tuple[bool, str]:
    """May this entry be transferred NOW, under these (freshly loaded) settings?

    Fail closed: anything unexpected -> not allowed. A future sender must load the
    settings again and call this right before sending each entry.
    """
    from collector.permissions.path_guard import PathGuard  # avoid import cycle

    active = config.grant_key()
    if active is None:
        return False, "no folder is approved"
    if config.paused:
        return False, "collection is paused"
    if entry.get("transfer_allowed") is False:
        return False, "quarantined: " + str(entry.get("quarantine_reason", "not authorized"))
    collected_under = _entry_grant(entry)
    if collected_under is None:
        return False, "collected before approval tracking existed"
    if collected_under != active:
        return False, "collected under an earlier or different approval"
    source = entry.get("source")
    if not isinstance(source, str) or not source:
        return False, "entry has no source"
    if not PathGuard(config).check(Path(active[0]) / source).allowed:
        return False, "source file is no longer allowed (excluded, moved or missing)"
    return True, "allowed"


def quarantine_unauthorized(outbox: Path, config: CollectorConfig) -> list[str]:
    """Isolate entries that were collected under an approval that is no longer active.

    Pause does NOT quarantine (it is temporary; transfer_gate still blocks while paused).
    Returns the source names that were isolated. Nothing is deleted: the entry is moved,
    with transfer_allowed = false, into the quarantine folder.
    """
    if not outbox.exists():
        return []
    active = config.grant_key()
    moved = []
    for path in sorted(outbox.glob("*.json")):
        try:
            entry = _read_json(path)
        except (OSError, ValueError, RuntimeError):
            entry = None
        if entry is not None and _entry_grant(entry) == active and active is not None:
            continue
        if entry is None:
            # Damaged file: move it as-is (no rewriting, so nothing is lost).
            target_dir = quarantine_dir(outbox)
            target_dir.mkdir(parents=True, exist_ok=True)
            target, n = target_dir / (path.stem + ".damaged"), 1
            while target.exists():
                target = target_dir / f"{path.stem}-{n}.damaged"
                n += 1
            os.replace(path, target)
            moved.append(path.name)
            continue
        if _entry_grant(entry) is None:
            reason = "collected before approval tracking existed"
        elif active is None:
            reason = "permission revoked"
        else:
            reason = "approved folder or approval changed"
        _isolate(outbox, path, entry, reason)
        moved.append(entry.get("source") or path.name)
    return moved


def _isolate(outbox: Path, path: Path, entry: dict, reason: str) -> None:
    target_dir = quarantine_dir(outbox)
    target_dir.mkdir(parents=True, exist_ok=True)
    entry["transfer_allowed"] = False
    entry["quarantine_reason"] = reason
    entry["quarantined_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target, n = target_dir / path.name, 1
    while target.exists():                       # never overwrite an earlier isolated entry
        target = target_dir / f"{path.stem}-{n}.json"
        n += 1
    _write_json(target, entry)
    path.unlink()                                # moved, not lost: the copy is in quarantine


def list_quarantine(outbox: Path) -> list[dict]:
    return list_entries(quarantine_dir(outbox))


def transferable_entries(outbox: Path, config: CollectorConfig) -> list[dict]:
    """The entries a sender would be allowed to send right now (for display and tests)."""
    return [e for e in list_entries(outbox) if transfer_gate(e, config)[0]]
