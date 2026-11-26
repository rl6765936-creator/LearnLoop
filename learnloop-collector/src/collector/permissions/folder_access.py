"""Granting, revoking and pausing the Collector's folder permission.

Every function here changes ONLY the settings file. None of them reads study files.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from collector.config import DEFAULT_CONFIG_PATH, load_config, save_config
from collector.permissions.path_guard import _norm, is_inside, is_link


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


def forbidden_reason(folder: Path, home: Path | None = None) -> str | None:
    """Why this folder may never be approved, or None if it is acceptable.

    Spec [APPROVED]: no broad computer access, no Downloads folder.
    The system-folder list is a [RECOMMENDATION].
    """
    home = home or Path.home()

    if _norm(folder) == _norm(Path(folder.anchor)):
        return "a whole drive cannot be approved"
    if is_inside(home, folder):
        return "the home folder (or a folder containing it) is too broad"
    if is_inside(folder, home / "Downloads"):
        return "the Downloads folder is not allowed"
    if is_inside(folder, home / "AppData"):
        return "application data folders are not allowed"
    for env_name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        system_dir = os.environ.get(env_name)
        if system_dir and is_inside(folder, system_dir):
            return "system folders are not allowed"
    return None


def grant_folder(folder: str, config_path: Path = DEFAULT_CONFIG_PATH,
                 home: Path | None = None) -> ActionResult:
    """Approve ONE study folder. Replaces any previously approved folder."""
    path = Path(folder).expanduser()
    if not path.is_absolute():
        return ActionResult(False, "please give a full path (for example C:\\...)")
    if not path.exists():
        return ActionResult(False, "folder not found")
    if not path.is_dir():
        return ActionResult(False, "this is a file, not a folder")
    if is_link(path):
        return ActionResult(False, "links/shortcuts cannot be approved – choose the real folder")

    real = path.resolve(strict=True)
    reason = forbidden_reason(real, home)
    if reason:
        return ActionResult(False, reason)

    config = load_config(config_path).config
    if config.approved_folder != str(real):
        # A new approval act -> a new identity. Re-approving the folder that is ALREADY
        # approved keeps the same identity (nothing was revoked in between).
        config.grant_id = uuid.uuid4().hex
    if config.collections_folder != str(real):
        # Classifications describe folders of ANOTHER folder – they do not carry over.
        config.collections, config.live_since, config.collections_folder = {}, None, str(real)
    config.approved_folder = str(real)
    config.paused = False
    save_config(config, config_path)
    return ActionResult(True, f"approved: {real}")


def revoke_folder(config_path: Path = DEFAULT_CONFIG_PATH) -> ActionResult:
    config = load_config(config_path).config
    config.approved_folder = None
    config.grant_id = None
    save_config(config, config_path)
    return ActionResult(True, "permission revoked – the Collector reads nothing")


def set_transfer_permission(allowed: bool, config_path: Path = DEFAULT_CONFIG_PATH) -> ActionResult:
    """Approve / revoke sending FILTERED content of the approved folder to the AI (spec §27).
    Separate from the read permission; given for the current approval only."""
    config = load_config(config_path).config
    key = config.grant_key()
    if allowed and key is None:
        return ActionResult(False, "approve a study folder first")
    config.transfer_approval = [key[0], key[1]] if allowed else None
    save_config(config, config_path)
    return ActionResult(True, "transfer to the AI allowed for the approved folder" if allowed
                        else "transfer to the AI revoked")


def set_paused(paused: bool, config_path: Path = DEFAULT_CONFIG_PATH) -> ActionResult:
    config = load_config(config_path).config
    config.paused = paused
    save_config(config, config_path)
    return ActionResult(True, "collection paused" if paused else "collection resumed")


def add_exclusion(pattern: str, config_path: Path = DEFAULT_CONFIG_PATH) -> ActionResult:
    pattern = pattern.strip()
    if not pattern:
        return ActionResult(False, "empty exclusion rule")
    config = load_config(config_path).config
    if pattern not in config.exclusions:
        config.exclusions.append(pattern)
        save_config(config, config_path)
    return ActionResult(True, f"excluded: {pattern}")


def remove_exclusion(pattern: str, config_path: Path = DEFAULT_CONFIG_PATH) -> ActionResult:
    config = load_config(config_path).config
    if pattern not in config.exclusions:
        return ActionResult(False, f"no such exclusion: {pattern}")
    config.exclusions.remove(pattern)
    save_config(config, config_path)
    return ActionResult(True, f"exclusion removed: {pattern}")


def choose_folder_dialog() -> str | None:
    """Open a Windows folder picker. Returns the chosen path, or None if cancelled.

    tkinter is part of Python's standard library – nothing is installed.
    """
    import tkinter as tk
    from tkinter import filedialog

    window = tk.Tk()
    window.withdraw()                    # hide the empty main window
    window.attributes("-topmost", True)  # show the dialog in front
    try:
        chosen = filedialog.askdirectory(title="Choose your LearnLoop study folder",
                                         mustexist=True)
    finally:
        window.destroy()
    return chosen or None
