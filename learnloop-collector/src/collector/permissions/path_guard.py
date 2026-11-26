"""PathGuard – the single gate every file access must pass through.

Rule: before the Collector touches ANY path, it asks PathGuard.check(path).
If the answer is "not allowed", the path is not opened, not listed, not read.

Checks, in order (cheapest and safest first):
  1. Is any folder approved at all?
  2. Is collection paused?
  3. Is the approved folder still a real, unchanged folder?
  4. Does the path stay inside the approved folder (no "..", no other drive)?
  5. Is it excluded (built-in rules + user rules)?  -> checked BEFORE reading
  6. Does any part of the path go through a symbolic link / junction?
  7. After resolving the real location, is it still inside? (defense in depth)
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path, PurePath

from collector.config import CollectorConfig

# Always excluded, even if the user's exclusion list is empty.
# Spec [APPROVED]: exclude .env files, keys and credentials.
# The exact list is a [RECOMMENDATION] – easy to extend.
BUILTIN_EXCLUSIONS: tuple[str, ...] = (
    ".env", ".env.*",
    "*.pem", "*.key", "*.pfx", "*.p12",
    "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "credentials*", "*.kdbx",
    ".git", ".ssh", ".aws", ".venv", "venv",
    # Temporary / lock files created by Office and other editors while a file is open.
    # They are not real study material and are often half-written.
    "~$*", "~*.tmp", ".~lock.*", "*.tmp",
    # Windows system files.
    "Thumbs.db", "desktop.ini",
)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    path: Path | None = None


def _norm(p: os.PathLike | str) -> str:
    """Normalize for comparison: removes '..', and on Windows ignores letter case."""
    return os.path.normcase(os.path.normpath(str(p)))


def is_inside(child: os.PathLike | str, parent: os.PathLike | str) -> bool:
    """True if child is parent itself or somewhere below it.

    Uses commonpath on whole path components, NOT string startswith:
    'C:/study2' must not count as inside 'C:/study'.
    """
    c, p = _norm(child), _norm(parent)
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives on Windows, e.g. C: vs D:
        return False


def is_link(p: Path) -> bool:
    """Symbolic link, or (Windows) a junction – both can point anywhere."""
    if p.is_symlink():
        return True
    is_junction = getattr(p, "is_junction", None)  # Python 3.12+
    return bool(is_junction and is_junction())


def matching_exclusion(relative: PurePath, patterns: list[str] | tuple[str, ...]) -> str | None:
    """Return the first exclusion pattern that matches, or None.

    A pattern matches if it is literally the relative path or one of its parts,
    or if, as a wildcard pattern, it matches the whole relative path (e.g. 'notes/private')
    or any single part of it (e.g. '.env', '*.key', 'secrets' as a folder name).
    Matching ignores case, like Windows does.
    """
    rel = relative.as_posix().lower()
    parts = [part.lower() for part in relative.parts]
    for pattern in patterns:
        pat = pattern.strip().replace("\\", "/").rstrip("/").lower()
        if not pat:
            continue
        # Literal match first: a rule that is exactly a file/folder name or path must
        # exclude it even if the name contains wildcard characters. For fnmatch "[1]"
        # means "the character 1", so "notes[1].txt" would otherwise never exclude itself.
        if rel == pat or pat in parts:
            return pattern
        if fnmatch.fnmatchcase(rel, pat) or rel.startswith(pat + "/"):
            return pattern
        if any(fnmatch.fnmatchcase(part, pat) for part in parts):
            return pattern
    return None


class PathGuard:
    """Decides whether a path may be accessed under the given settings."""

    def __init__(self, config: CollectorConfig):
        self._config = config

    def check(self, path: os.PathLike | str) -> AccessDecision:
        cfg = self._config

        if cfg.approved_folder is None:
            return AccessDecision(False, "no folder is approved")
        if cfg.paused:
            return AccessDecision(False, "collection is paused")

        root = Path(cfg.approved_folder)
        if not root.is_dir():
            return AccessDecision(False, "approved folder no longer exists")
        if is_link(root) or _norm(root.resolve()) != _norm(root):
            return AccessDecision(False, "approved folder changed or became a link")

        given = Path(path)
        candidate = Path(os.path.normpath(given if given.is_absolute() else root / given))
        if not is_inside(candidate, root):
            return AccessDecision(False, "outside the approved folder")

        relative = PurePath(os.path.relpath(candidate, root))
        if relative == PurePath("."):
            relative = PurePath()

        rule = matching_exclusion(relative, BUILTIN_EXCLUSIONS + tuple(cfg.exclusions))
        if rule is not None:
            return AccessDecision(False, f"excluded by rule '{rule}'")

        current = root
        for part in relative.parts:
            current = current / part
            if is_link(current):
                return AccessDecision(False, "path goes through a symbolic link or junction")

        if not candidate.exists():
            return AccessDecision(False, "not found")

        real = candidate.resolve(strict=True)
        if not is_inside(real, root):  # should never happen after the checks above
            return AccessDecision(False, "real location is outside the approved folder")
        if not (real.is_file() or real.is_dir()):
            return AccessDecision(False, "not a regular file or folder")

        return AccessDecision(True, "allowed", real)
