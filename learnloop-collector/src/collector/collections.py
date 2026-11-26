"""Explicit classification of folders ("collections") inside the ONE approved folder.

User decision 2026-11-24 [APPROVED]:
- The user marks folders (e.g. "שנה א'") as material she already FINISHED studying, as
  material she studies NOW, as not studied yet – or leaves them unknown.
- This is explicit information. The Collector never guesses it from names or content; it
  only applies the user's setting mechanically (spec §2: the Collector does not judge
  educational meaning).
- Unclassified material stays "unknown". New, unclassified folders are reported ONCE,
  grouped per folder (never per file).
- Classification needs NO extra permission: it only labels folders inside the approved folder.

Two separate things are attached to every file:
  study_status  – from the classification above (what the material IS for the user)
  queue         – live | backfill, from TIME only: new or changed after `live_since` = live
                  (processed first); older material = backfill (gradual, daily budget).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from collector.config import DEFAULT_CONFIG_PATH, LOCAL_DATA, STUDY_STATUSES, load_config, save_config
from collector.monitor.scanner import scan_folder
from collector.permissions.path_guard import PathGuard

ROOT_KEY = ""                                    # files directly in the approved folder
SEEN_PATH = LOCAL_DATA / "collections_seen.json"


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def classify(rel: str, collections: dict) -> dict:
    """Which collection a file belongs to: the LONGEST classified folder that contains it;
    otherwise its top-level folder, unknown."""
    parts = PurePosixPath(rel).parts
    for n in range(len(parts) - 1, 0, -1):
        key = "/".join(parts[:n])
        if key in collections:
            c = collections[key]
            return {"collection": key, "study_status": c["study_status"], "period": c.get("period")}
    return {"collection": parts[0] if len(parts) > 1 else ROOT_KEY, "study_status": "unknown", "period": None}


def queue_for(modified_at: str | None, live_since: str | None) -> str:
    """live = new or changed after ongoing work started. Before the initial classification
    started (live_since unset) EVERYTHING is backfill – nothing jumps the budget."""
    if live_since is None or modified_at is None:
        return "backfill"
    return "live" if modified_at >= live_since else "backfill"


def overview(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Local, read-only overview for the initial classification: top-level folders and
    classified folders with file counts. File CONTENT is never opened."""
    config = load_config(config_path).config
    if config.approved_folder is None:
        return {"error": "no folder is approved"}
    if config.paused:
        return {"error": "collection is paused"}
    collections = config.active_collections()
    scan = scan_folder(Path(config.approved_folder), PathGuard(config))
    groups: dict[str, dict] = {}
    for rel in scan.files:
        c = classify(rel, collections)
        g = groups.setdefault(c["collection"], {"collection": c["collection"], "study_status": c["study_status"],
                                                "period": c["period"], "files": 0, "subfolders": set()})
        g["files"] += 1
        parts = PurePosixPath(rel).parts
        depth = len(PurePosixPath(c["collection"]).parts) if c["collection"] else 0
        if len(parts) > depth + 1:
            g["subfolders"].add(parts[depth])
    for key, c in collections.items():                    # classified but (still) empty
        groups.setdefault(key, {"collection": key, "study_status": c["study_status"], "period": c.get("period"),
                                "files": 0, "subfolders": set()})
    rows = [dict(g, subfolders=len(g["subfolders"])) for g in sorted(groups.values(), key=lambda g: g["collection"])]
    return {"live_since": config.live_since, "collections": rows,
            "unclassified": [r["collection"] for r in rows if r["study_status"] == "unknown"]}


def set_collection(folder: str, status: str, period: str | None = None,
                   config_path: Path = DEFAULT_CONFIG_PATH) -> tuple[bool, str]:
    if status not in STUDY_STATUSES:
        return False, "status must be one of: " + ", ".join(STUDY_STATUSES)
    config = load_config(config_path).config
    if config.approved_folder is None:
        return False, "no folder is approved"
    root = Path(config.approved_folder)
    target = Path(folder) if Path(folder).is_absolute() else root / folder
    decision = PathGuard(config).check(target)
    if not decision.allowed:
        return False, f"not allowed: {decision.reason}"
    if not decision.path.is_dir() or decision.path == root.resolve():
        return False, "choose a folder INSIDE the approved folder"
    key = Path(os.path.relpath(decision.path, root.resolve())).as_posix()
    if period is not None and len(period) > 80:
        return False, "period: at most 80 characters"
    if config.collections_folder != config.approved_folder:
        config.collections, config.collections_folder = {}, config.approved_folder
    if status == "unknown":
        config.collections.pop(key, None)
    else:
        config.collections[key] = {"study_status": status, "period": period}
    save_config(config, config_path)
    return True, f"'{key}' is now: {status}" + (f" (period: {period})" if period else "")


def start_live(config_path: Path = DEFAULT_CONFIG_PATH, now: float | None = None) -> tuple[bool, str]:
    """Mark the start of ongoing work. Set once; never moved silently."""
    config = load_config(config_path).config
    if config.approved_folder is None:
        return False, "no folder is approved"
    if config.live_since:
        return False, f"ongoing work already started at {config.live_since}"
    config.live_since = utc_iso(now if now is not None else datetime.now(timezone.utc).timestamp())
    if config.collections_folder != config.approved_folder:
        config.collections, config.collections_folder = {}, config.approved_folder
    save_config(config, config_path)
    return True, (f"ongoing work starts now ({config.live_since}): files added or changed from now on are "
                  f"processed first; existing files go to the gradual backfill queue")


def new_unclassified(rows: list[dict], seen_path: Path = SEEN_PATH) -> list[str]:
    """Unclassified collections not reported before. Remembered, so the same question is not
    asked again without a reason (a folder that is classified and later reset is asked again)."""
    try:
        seen = set(json.loads(seen_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    fresh = [r["collection"] for r in rows if r["study_status"] == "unknown" and r["files"] > 0
             and r["collection"] not in seen]
    if fresh:
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = seen_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(seen | set(fresh)), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, seen_path)
    return fresh


def progress(config_path: Path = DEFAULT_CONFIG_PATH, outbox_dir: Path | None = None,
             transfer_state_path: Path | None = None, monitor_state_path: Path | None = None) -> dict:
    """How far the gradual backfill is, per collection – counts only, read-only.
    'done' = the file's current version was acknowledged by the brain."""
    from collector.monitor.state import DEFAULT_STATE_PATH, load_state
    from collector.security import outbox
    from collector.transfer.client import DEFAULT_STATE, _load_state
    config = load_config(config_path).config
    if config.approved_folder is None:
        return {"error": "no folder is approved"}
    tstate = _load_state(transfer_state_path or DEFAULT_STATE)
    done_files = tstate.get("files", {})
    mtimes = {rel: r.mtime_ns for rel, r in load_state(monitor_state_path or DEFAULT_STATE_PATH)[0].files.items()}
    collections = config.active_collections()
    rows: dict[tuple, dict] = {}
    for entry in outbox.list_entries(outbox_dir or outbox.DEFAULT_OUTBOX):
        rel = entry.get("source")
        modified_at = utc_iso(mtimes[rel] / 1e9) if rel in mtimes else None
        c = classify(rel, collections)
        q = queue_for(modified_at, config.live_since)
        row = rows.setdefault((q, c["collection"]), {"queue": q, "collection": c["collection"],
                                                     "study_status": c["study_status"], "files": 0, "done": 0})
        row["files"] += 1
        known = done_files.get(rel)
        if known and known["sha"] == entry.get("sha256") and known["grant"] == config.grant_id:
            row["done"] += 1
    backfill = [r for r in rows.values() if r["queue"] == "backfill"]
    total, done = sum(r["files"] for r in backfill), sum(r["done"] for r in backfill)
    return {"live_since": config.live_since, "rows": sorted(rows.values(), key=lambda r: (r["queue"], r["collection"])),
            "backfill_files": total, "backfill_done": done,
            "backfill_percent": round(100 * done / total, 1) if total else 100.0,
            "budget_today": tstate.get("backfill", {}), "daily_limit_chars": config.backfill_daily_chars}
