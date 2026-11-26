"""Git source (spec §24/§26/§53; decision 2026-11-24 Q11 – APPROVED).

What is read: from repositories she approves ONE BY ONE – commit messages and the code changes
(diffs) of each commit. Nothing else (no branches of other people, no config, no hooks).

How – and what is never done:
- With `dulwich`, a Python library that reads the repository's files directly. The `git`
  program is NOT started; no hook, filter, command or code from the repository runs.
- Every message and diff passes the SAME local filter as study files (security/filter.py):
  definite secrets are removed; anything uncertain stops that commit (fail closed, §29).
- A repository in a forbidden place (Downloads, AppData, home or system folders) is refused,
  with the same rules as the study folder (folder_access.forbidden_reason).
- Its own permission per repository (§27), separate from the study folder's.
"""

from __future__ import annotations

import io
import secrets
from dataclasses import dataclass, field
from pathlib import Path

MAX_COMMITS = 50                 # per repository per read
MAX_DIFF_CHARS = 60_000          # per commit; longer diffs are cut and marked
SKIPPED_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".zip", ".exe", ".dll", ".pdf", ".lock")


class GitUnavailable(RuntimeError):
    pass


@dataclass
class CommitText:
    commit_id: str
    committed_at: int
    message: str
    diff: str
    status: str                            # clean | redacted | pending_review
    redactions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _dulwich():
    try:
        from dulwich.patch import write_tree_diff
        from dulwich.repo import Repo
    except ImportError as e:
        raise GitUnavailable("the dulwich library is not installed (pip install -r requirements.txt)") from e
    return Repo, write_tree_diff


def check_repository(path: str, home: Path | None = None) -> tuple[bool, str, Path | None]:
    from collector.permissions.folder_access import forbidden_reason
    p = Path(path)
    if not p.is_absolute():
        return False, "give the full path of the repository", None
    real = p.resolve()
    reason = forbidden_reason(real, home)
    if reason:
        return False, reason, None
    if not (real / ".git").is_dir():
        return False, "not a Git repository (no .git folder)", None
    return True, "ok", real


def new_grant() -> str:
    return "git-" + secrets.token_hex(8)


def _diff_text(repo, write_tree_diff, commit) -> tuple[str, list[str]]:
    warnings = []
    parent_tree = repo[commit.parents[0]].tree if commit.parents else None
    out = io.BytesIO()
    write_tree_diff(out, repo.object_store, parent_tree, commit.tree)
    text = out.getvalue().decode("utf-8", errors="replace")
    kept, skip = [], False
    for line in text.splitlines():
        if line.startswith("diff --git"):
            skip = line.lower().endswith(SKIPPED_SUFFIXES) or "/.env" in line or line.endswith(".env")
            if skip:
                warnings.append("a binary/secret-like file was skipped")
        if not skip:
            kept.append(line)
    text = "\n".join(kept)
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS]
        warnings.append(f"diff cut at {MAX_DIFF_CHARS:,} characters")
    return text, warnings


def read_commits(repo_path: Path, seen: set[str] | None = None, max_commits: int = MAX_COMMITS) -> list[CommitText]:
    """Newest first. Commits in `seen` are not read again. Read-only."""
    from collector.readers.base import ExtractionResult, Segment
    from collector.security.filter import filter_result
    Repo, write_tree_diff = _dulwich()
    seen = seen or set()
    repo = Repo(str(repo_path))
    out = []
    try:
        try:
            walker = repo.get_walker(max_entries=max_commits)
        except KeyError:                       # empty repository (no commits yet)
            return []
        for entry in walker:
            c = entry.commit
            cid = c.id.decode("ascii")
            if cid in seen:
                continue
            message = c.message.decode("utf-8", errors="replace")
            diff, warnings = _diff_text(repo, write_tree_diff, c)
            result = ExtractionResult(Path(repo_path), "ok", "git",
                                      [Segment(f"commit {cid[:10]} message", message),
                                       Segment(f"commit {cid[:10]} diff", diff)])
            outcome = filter_result(result)
            texts = [s.text for s in outcome.segments]
            out.append(CommitText(cid, int(c.commit_time),
                                  texts[0] if texts else "", texts[1] if len(texts) > 1 else "",
                                  outcome.status, [r.kind for r in outcome.redactions], warnings))
    finally:
        repo.close()
    return out
