"""Walk the approved folder and list every file PathGuard allows.

Excluded folders are skipped entirely (we do not even look inside them).
Links are never followed.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from collector.permissions.path_guard import PathGuard

Signature = tuple[int, int]  # (size, mtime_ns) – cheap "has it changed?" check


@dataclass
class ScanResult:
    files: dict[str, Signature] = field(default_factory=dict)
    blocked: list[tuple[str, str]] = field(default_factory=list)  # (relative path, reason)


def scan_folder(root: Path, guard: PathGuard) -> ScanResult:
    result = ScanResult()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        allowed_dirs = []
        for name in dirnames:
            decision = guard.check(current / name)
            if decision.allowed:
                allowed_dirs.append(name)
            else:
                result.blocked.append((_rel(current / name, root), decision.reason))
        dirnames[:] = allowed_dirs  # os.walk will only enter allowed folders

        for name in filenames:
            path = current / name
            decision = guard.check(path)
            if not decision.allowed:
                result.blocked.append((_rel(path, root), decision.reason))
                continue
            try:
                st = path.stat()
            except OSError:
                continue  # disappeared during the scan – next cycle will see it
            result.files[_rel(path, root)] = (st.st_size, st.st_mtime_ns)
    return result


def hash_file(path: Path, guard: PathGuard) -> str | None:
    """SHA-256 of the file content, read-only and in chunks. None if not allowed/readable."""
    decision = guard.check(path)
    if not decision.allowed:
        return None
    digest = hashlib.sha256()
    try:
        with open(decision.path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _rel(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()
