"""Manual upload (spec §24/§54; decision 2026-11-24 Q13 – APPROVED).

Each upload is a ONE-TIME approval for ONE file: she names the file and confirms. It does not
create a lasting permission, and the file's folder does not become an approved folder.
The same rules as the study folder apply: forbidden places refused (Downloads, AppData, system),
built-in exclusions (.env, keys …), the same readers, the same secret filter; anything uncertain
is NOT sent (fail closed).
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from collector.config import CollectorConfig
from collector.permissions.folder_access import forbidden_reason
from collector.permissions.path_guard import PathGuard
from collector.readers.extract import extract_file
from collector.security.filter import PENDING_REVIEW, filter_result
from collector.transfer.envelope import build_envelopes


def prepare(path: str, home: Path | None = None, ocr=None) -> tuple[list[dict] | None, str]:
    """Returns (envelopes, message). envelopes None = nothing may be sent."""
    p = Path(path)
    if not p.is_absolute() or not p.is_file():
        return None, "give the full path of an existing file"
    real = p.resolve()
    reason = forbidden_reason(real.parent, home)
    if reason:
        return None, f"not allowed: {reason}"
    grant = "manual-" + secrets.token_hex(8)                     # this one upload only
    guard = PathGuard(CollectorConfig(approved_folder=str(real.parent), grant_id=grant))
    result = extract_file(real, guard, ocr=ocr)
    if result.status != "ok":
        return None, f"could not read the file: {result.error or result.status}"
    outcome = filter_result(result)
    if outcome.status == PENDING_REVIEW:
        return None, "possible sensitive data found – the file was NOT sent (check it and remove the data first)"
    digest = hashlib.sha256(real.read_bytes()).hexdigest()
    entry = {"source": "uploads/" + real.name, "sha256": digest,
             "segments": [{"location": s.location, "text": s.text} for s in outcome.segments],
             "filter_status": outcome.status,
             "redactions": [{"kind": r.kind, "location": r.location} for r in outcome.redactions],
             "reader": result.reader, "warnings": list(result.warnings)}
    envs = build_envelopes(entry, "manual", grant, "new",
                           context={"collection": "uploads", "study_status": "unknown", "period": None,
                                    "queue": "live", "modified_at": None})
    for env in envs:
        env["source"]["type"] = "manual"
    msg = f"ready: {len(envs)} part(s)" + (f", {len(outcome.redactions)} secret(s) removed" if outcome.redactions else "")
    return envs, msg
