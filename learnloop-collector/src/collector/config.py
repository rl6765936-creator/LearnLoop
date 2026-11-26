"""Load and save the Collector's local settings.

Security principle (fail closed): if the settings file is missing, corrupted
or contains unexpected values, the Collector falls back to the DEFAULT state,
in which NO folder is approved and therefore nothing is read.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Project root = two levels above this file: src/collector/config.py -> project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Where settings and state are kept (ASSUMPTION – final storage location is OPEN in the spec).
# - Running from source: <project>/local_data
# - Running as a packaged .exe: next to the .exe. (Inside a one-file .exe, __file__ points
#   to a temporary folder that is deleted on exit – settings saved there would be lost.)
def local_data_dir(frozen: bool, executable: str) -> Path:
    if frozen:
        return Path(executable).resolve().parent / "local_data"
    return PROJECT_ROOT / "local_data"


LOCAL_DATA = local_data_dir(getattr(sys, "frozen", False), sys.executable)

DEFAULT_CONFIG_PATH = LOCAL_DATA / "settings.json"

CONFIG_VERSION = 1

# User decision 2026-11-24: Study Status is EXPLICIT information, set by the user.
STUDY_STATUSES = ("completed", "active", "not_studied", "unknown")


@dataclass
class CollectorConfig:
    """All settings the Collector keeps between runs."""

    approved_folder: str | None = None          # None = no permission granted
    exclusions: list[str] = field(default_factory=list)
    paused: bool = False
    version: int = CONFIG_VERSION
    # Identity of ONE approval act. A new grant (after revoke, or of another folder) gets a
    # new id, so content collected under an older approval can be told apart and blocked
    # from transfer (user decision 2026-11-24, Q3 option A). None = approval saved before
    # this field existed ("legacy" approval) – still a single, consistent identity.
    grant_id: str | None = None
    # AI transfer permission (spec §27 APPROVED: separate from read permission, per_source).
    # Bound to ONE approval: [folder, grant_id] it was given for. A revoke or a new grant
    # changes the approval, so the transfer permission lapses automatically.
    transfer_approval: list | None = None
    # EXPLICIT classification of folders inside the approved folder (user decision
    # 2026-11-24). Key: folder path relative to the approved folder ("שנה א'", "C#/2026").
    # Value: {"study_status": completed | active | not_studied | unknown, "period": text|None}.
    # The Collector never infers this: it only applies what the user set. Unclassified = unknown.
    collections: dict = field(default_factory=dict)
    collections_folder: str | None = None   # the approved folder these classifications belong to
    # When ongoing ("live") work started: files new or changed after it go to the live queue,
    # older ones to the gradual backfill queue. None = initial classification not started.
    live_since: str | None = None
    # Daily limit for backfill transfer, in characters of filtered text [ASSUMPTION ~100k tokens].
    backfill_daily_chars: int = 300_000
    # Local link to the brain on THIS computer (decision 2026-11-24, Q8): {"port": 8765,
    # "token_file": "<path to the brain's local_data/ingest.token>"}. None = no link (dry run only).
    brain_link: dict | None = None
    # ActivityWatch (decision 2026-11-24 Q12): its OWN read permission, separate from the folder.
    # {"grant_id": "aw-…"} when approved; None = not approved (nothing is read from it).
    activitywatch: dict | None = None
    # Git repositories she approved ONE BY ONE (decision 2026-11-24 Q11): [{"path", "grant_id"}].
    git_repos: list = field(default_factory=list)

    def active_collections(self) -> dict:
        """Classifications that belong to the CURRENTLY approved folder (else none)."""
        if self.approved_folder is None or self.collections_folder != self.approved_folder:
            return {}
        return self.collections

    def transfer_allowed(self) -> bool:
        key = self.grant_key()
        return key is not None and self.transfer_approval == [key[0], key[1]]

    def grant_key(self) -> tuple[str, str | None] | None:
        """(folder, grant id) of the active approval, or None if nothing is approved."""
        if self.approved_folder is None:
            return None
        return (self.approved_folder, self.grant_id)


@dataclass
class LoadResult:
    """Result of loading settings: the config + a warning if we had to fall back."""

    config: CollectorConfig
    warning: str | None = None


def _validate(data: object) -> CollectorConfig:
    """Turn raw JSON data into a CollectorConfig, or raise ValueError.

    We check every field's type ourselves instead of trusting the file,
    because the file could be edited by hand or damaged.
    """
    if not isinstance(data, dict):
        raise ValueError("settings must be a JSON object")

    approved = data.get("approved_folder")
    if approved is not None and not isinstance(approved, str):
        raise ValueError("approved_folder must be text or null")
    if isinstance(approved, str) and not approved.strip():
        raise ValueError("approved_folder must not be empty")

    exclusions = data.get("exclusions", [])
    if not isinstance(exclusions, list) or not all(isinstance(x, str) for x in exclusions):
        raise ValueError("exclusions must be a list of text values")

    paused = data.get("paused", False)
    if not isinstance(paused, bool):
        raise ValueError("paused must be true or false")

    grant_id = data.get("grant_id")
    if grant_id is not None and (not isinstance(grant_id, str) or not grant_id.strip()):
        raise ValueError("grant_id must be text or null")
    if approved is None:
        grant_id = None  # no approval -> no approval identity

    transfer = data.get("transfer_approval")
    if transfer is not None and not (isinstance(transfer, list) and len(transfer) == 2
                                     and isinstance(transfer[0], str)
                                     and (transfer[1] is None or isinstance(transfer[1], str))):
        raise ValueError("transfer_approval must be [folder, grant_id] or null")

    collections = data.get("collections", {})
    if not isinstance(collections, dict):
        raise ValueError("collections must be an object")
    for key, value in collections.items():
        if not isinstance(key, str) or not key.strip() or ".." in key.replace("\\", "/").split("/") \
                or key.startswith(("/", "\\")) or ":" in key:
            raise ValueError("collections keys must be folder paths relative to the approved folder")
        if not isinstance(value, dict) or value.get("study_status") not in STUDY_STATUSES:
            raise ValueError("collections values need a valid study_status")
        period = value.get("period")
        if period is not None and (not isinstance(period, str) or len(period) > 80):
            raise ValueError("period must be short text or null")

    live_since = data.get("live_since")
    if live_since is not None and not isinstance(live_since, str):
        raise ValueError("live_since must be text or null")

    budget = data.get("backfill_daily_chars", 300_000)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        raise ValueError("backfill_daily_chars must be a non-negative whole number")
    collections_folder = data.get("collections_folder")
    if collections_folder is not None and not isinstance(collections_folder, str):
        raise ValueError("collections_folder must be text or null")

    link = data.get("brain_link")
    if link is not None and not (isinstance(link, dict) and isinstance(link.get("port"), int)
                                 and not isinstance(link.get("port"), bool)
                                 and isinstance(link.get("token_file"), str) and set(link) <= {"port", "token_file"}):
        raise ValueError('brain_link must be {"port": number, "token_file": text} or null')

    aw = data.get("activitywatch")
    if aw is not None and not (isinstance(aw, dict) and {"grant_id"} <= set(aw) <= {"grant_id", "transfer"}
                               and isinstance(aw["grant_id"], str) and 0 < len(aw["grant_id"]) <= 64
                               and isinstance(aw.get("transfer", False), bool)):
        raise ValueError('activitywatch must be {"grant_id": text, "transfer": true/false} or null')

    repos = data.get("git_repos", [])
    if not (isinstance(repos, list) and all(isinstance(r, dict) and {"path", "grant_id"} <= set(r) <= {"path", "grant_id", "transfer"}
                                            and isinstance(r["path"], str) and isinstance(r["grant_id"], str)
                                            and isinstance(r.get("transfer", False), bool)
                                            for r in repos)):
        raise ValueError('git_repos must be a list of {"path": text, "grant_id": text, "transfer": true/false}')

    return CollectorConfig(approved_folder=approved, exclusions=exclusions, paused=paused,
                           grant_id=grant_id, transfer_approval=transfer, collections=collections,
                           collections_folder=collections_folder, live_since=live_since, backfill_daily_chars=budget,
                           brain_link=link, activitywatch=aw, git_repos=repos)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> LoadResult:
    """Read settings from disk. Never raises: on any problem returns the safe default."""
    if not path.exists():
        # First run – normal situation, not an error.
        return LoadResult(CollectorConfig())

    from collector.security import at_rest     # §57: settings are encrypted at rest (DPAPI)
    try:
        data = at_rest.read(path)                   # also reads an older plain-JSON file
        return LoadResult(_validate(data))
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as e:
        return LoadResult(
            CollectorConfig(),
            warning=f"Settings file could not be used ({e}). No folder is approved.",
        )


def save_config(config: CollectorConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Write settings safely: write a temporary file first, then replace the real one.

    os.replace is atomic: the real file is either the old version or the new one,
    never half-written – even if the program crashes in the middle.
    """
    # Encrypted at rest (security/at_rest.py: Windows DPAPI) – the file reveals which folder is
    # approved and what is excluded. at_rest.write is atomic too (temporary file, then replace).
    from collector.security import at_rest
    at_rest.write(path, asdict(config))
