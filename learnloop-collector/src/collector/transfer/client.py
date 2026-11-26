"""TransferClient – the ONLY part of the Collector that hands content to a transport.

For every envelope, right before it is handed over (spec §28: revoke acts immediately,
also in the middle of a cycle), the LIVE settings are read again and must allow it:
  1. a folder is approved and not paused, and the AI-transfer permission (spec §27,
     per_source) was given for THIS approval;
  2. outbox.transfer_gate() allows the entry (approval tag, quarantine, PathGuard).
State (local_data/transfer_state.json, no content): which envelopes were acknowledged –
those are never sent again (spec §35: no resending of processed material). Not
acknowledged / offline -> stays queued for the next cycle (spec §37 local_queue).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from datetime import datetime, timezone

from collector.collections import classify, queue_for, utc_iso
from collector.config import DEFAULT_CONFIG_PATH, LOCAL_DATA, load_config
from collector.monitor.state import DEFAULT_STATE_PATH, load_state
from collector.security import outbox
from collector.transfer.envelope import build_envelopes, removal_envelope, source_id
from collector.transfer.transports import TransportUnavailable

DEFAULT_STATE = LOCAL_DATA / "transfer_state.json"


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else {"files": {}, "acked": []}
    except (OSError, ValueError):
        return {"files": {}, "acked": []}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class TransferClient:
    def __init__(self, transport, config_path: Path = DEFAULT_CONFIG_PATH,
                 outbox_dir: Path = outbox.DEFAULT_OUTBOX, state_path: Path = DEFAULT_STATE,
                 monitor_state_path: Path = DEFAULT_STATE_PATH, today=None):
        self.transport, self.config_path = transport, config_path
        self.outbox_dir, self.state_path = outbox_dir, state_path
        self.monitor_state_path = monitor_state_path
        self.today = today or (lambda: datetime.now(timezone.utc).date().isoformat())

    def _allowed(self, entry: dict | None) -> tuple[bool, str]:
        config = load_config(self.config_path).config          # LIVE settings, every time
        if not config.transfer_allowed():
            return False, "transfer to the AI is not allowed for this folder"
        if entry is not None:
            return outbox.transfer_gate(entry, config)
        if config.paused:
            return False, "collection is paused"
        return True, "allowed"

    def run(self) -> dict:
        report = {"transport": self.transport.name, "sent": 0, "acknowledged": 0, "skipped_known": 0,
                  "blocked": [], "queued": 0, "stopped": None}
        ok, why = self._allowed(None)
        if not ok:
            report["stopped"] = why
            return report
        config = load_config(self.config_path).config
        src, grant = source_id(config.approved_folder), config.grant_id
        state = _load_state(self.state_path)
        acked = set(state.get("acked", []))
        files = state.setdefault("files", {})       # rel -> {"sha": .., "grant": ..} last acknowledged version

        mtimes = {rel: r.mtime_ns for rel, r in load_state(self.monitor_state_path)[0].files.items()}
        collections = config.active_collections()
        live, backfill = [], []
        current = {}
        for entry in outbox.list_entries(self.outbox_dir):
            rel = entry.get("source")
            current[rel] = entry
            known = files.get(rel)
            if known and known["sha"] == entry.get("sha256") and known["grant"] == grant:
                continue
            change = "modified" if known and known["grant"] == grant else "new"
            modified_at = utc_iso(mtimes[rel] / 1e9) if rel in mtimes else None
            ctx = dict(classify(rel, collections), modified_at=modified_at,
                       queue=queue_for(modified_at, config.live_since))
            target = live if ctx["queue"] == "live" else backfill
            for env in build_envelopes(entry, src, grant, change, context=ctx):
                target.append((entry, env))
        for rel, known in list(files.items()):
            if rel not in current and known["grant"] == grant:
                live.append((None, removal_envelope(rel, known["sha"], src, grant)))
        # Live material first and never limited by the backfill budget; backfill folder by
        # folder (collection, then path), within the daily budget (decision 2026-11-24).
        backfill.sort(key=lambda w: (w[1]["context"]["collection"], w[1]["file"]["rel_path"],
                                     w[1]["chunks"][0]["index"] if w[1]["chunks"] else 0))
        budget = state.setdefault("backfill", {"day": None, "chars": 0})
        if budget.get("day") != self.today():
            budget.update(day=self.today(), chars=0)
        work = [(e, env, "live") for e, env in live] + [(e, env, "backfill") for e, env in backfill]
        report.update(live_pending=len(live), backfill_pending=len(backfill), backfill_deferred=0)

        for entry, env, queue in work:
            if env["idempotency_key"] in acked:
                report["skipped_known"] += 1
                continue
            size = sum(len(c["text"]) for c in env["chunks"])
            if queue == "backfill" and budget["chars"] + size > config.backfill_daily_chars:
                report["backfill_deferred"] += 1               # waits for tomorrow's budget
                continue
            ok, why = self._allowed(entry) if entry is not None else self._allowed(None)
            if not ok:                                          # revoked / paused mid-run
                report["blocked"].append(why)
                report["stopped"] = why
                break
            data = json.dumps(env, ensure_ascii=False).encode("utf-8")
            try:
                ack = self.transport.send(data)
            except TransportUnavailable:
                report["queued"] = len(work) - report["sent"] - report["skipped_known"]
                report["stopped"] = "offline – kept in the local queue"
                break
            report["sent"] += 1
            if queue == "backfill":
                budget["chars"] += size
            if ack.get("accepted"):
                report["acknowledged"] += 1
                acked.add(env["idempotency_key"])
                rel = env["file"]["rel_path"]
                if env["file"]["change"] == "removed":
                    files.pop(rel, None)
                elif env["chunks"] == [] or env["chunks"][-1]["index"] == env["chunks"][-1]["total"] - 1:
                    files[rel] = {"sha": env["file"]["sha256"], "grant": grant}   # last part acknowledged
        if self.transport.sends_data:
            state["acked"] = sorted(acked)
            _save_state(self.state_path, state)
        if hasattr(self.transport, "would_send"):
            report["would_send"] = self.transport.would_send
        return report
