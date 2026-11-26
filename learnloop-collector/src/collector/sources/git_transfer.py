"""Git commits -> the local brain (decision 2026-11-24, Q11 + Q8).

- Only repositories she approved AND whose transfer she switched on (`git transfer PATH on`) –
  read permission and transfer permission are separate (§27).
- Each commit becomes ordinary transfer envelopes (source.type "git"): message + diff, ALREADY
  filtered. A commit held by the filter (pending_review) is never sent.
- Sent commits are remembered per approval (local_data/git_sent.json), so nothing is sent twice
  and a NEW approval of the same repository starts fresh.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from collector.config import load_config
from collector.sources import git_repos
from collector.transfer.envelope import build_envelopes
from collector.transfer.transports import TransportUnavailable

STATE = Path(__file__).resolve().parents[3] / "local_data" / "git_sent.json"


def repo_source_id(path: str) -> str:
    return "git-" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def commit_envelopes(repo: dict, commit) -> list[dict]:
    entry = {"source": f"commits/{commit.commit_id[:12]}",
             "sha256": hashlib.sha256((commit.commit_id + commit.message + commit.diff).encode("utf-8")).hexdigest(),
             "segments": [{"location": f"commit {commit.commit_id[:10]} message", "text": commit.message},
                          {"location": f"commit {commit.commit_id[:10]} diff", "text": commit.diff or "(no text changes)"}],
             "filter_status": commit.status, "redactions": [{"kind": k, "location": "commit"} for k in commit.redactions],
             "reader": "git", "warnings": list(commit.warnings)}
    when = datetime.fromtimestamp(commit.committed_at, timezone.utc).isoformat()
    envs = build_envelopes(entry, repo_source_id(repo["path"]), repo["grant_id"], "new",
                           context={"collection": "git", "study_status": "unknown", "period": None,
                                    "queue": "live", "modified_at": when})
    for env in envs:
        env["source"]["type"] = "git"
    return envs


def send_commits(transport, config_path=None, state_path: Path = STATE, reader=None) -> dict:
    reader = reader or git_repos.read_commits
    cfg = load_config(config_path).config if config_path else load_config().config
    state = _load(state_path)
    report = {"sent": 0, "held": 0, "repos": 0, "stopped": None}
    for repo in cfg.git_repos:
        if not repo.get("transfer"):
            continue
        report["repos"] += 1
        sent = set(state.get(repo["grant_id"], []))
        try:
            commits = reader(Path(repo["path"]), seen=sent)
        except git_repos.GitUnavailable as e:
            report["stopped"] = str(e)
            break
        for c in reversed(commits):                          # oldest first
            if c.status == "pending_review":
                report["held"] += 1
                continue
            try:
                acks = [transport.send(json.dumps(env, ensure_ascii=False).encode("utf-8"))
                        for env in commit_envelopes(repo, c)]
            except TransportUnavailable:
                report["stopped"] = "the brain is not running – kept for later"
                break
            if all(a.get("accepted") for a in acks):
                sent.add(c.commit_id)
                report["sent"] += 1
        state[repo["grant_id"]] = sorted(sent)
        if report["stopped"]:
            break
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return report
