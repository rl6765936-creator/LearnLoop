"""Monitor.run_once() – one full detection-and-processing cycle.

Each cycle:
  1. Reload settings (so revoke / pause take effect immediately).
  2. Scan the approved folder (through PathGuard).
  3. Compare with what we remember -> new / changed / moved / deleted.
  4. Wait until a changed file is stable (not still being written).
  5. Process ONLY the files that really changed (by content fingerprint).
  6. Save what we remember.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from collector.config import DEFAULT_CONFIG_PATH, load_config
from collector.monitor.scanner import Signature, hash_file, scan_folder
from collector.monitor.state import (DEFAULT_STATE_PATH, REMOVED, FileRecord,
                                     MonitorState, load_state, save_state)
from collector.permissions.path_guard import PathGuard
from collector.readers import base
from collector.readers.base import ExtractionResult
from collector.readers.extract import extract_file
from collector.security import outbox
from collector.security.filter import PENDING_REVIEW, filter_result
from collector.readers.base import Segment
from collector.security.review import (APPROVE, DEFAULT_MANUAL_PATH, DEFAULT_REVIEW_PATH,
                                       IGNORE, load_decisions, load_manual)

IGNORED = "ignored"  # the user chose to ignore this version of the file


class _PermissionChanged(Exception):
    """Settings changed (revoke / pause / other folder) while a file was being processed."""

# Spec GAP: retry count and "settle" time are not defined. These are [ASSUMPTION]s.
MAX_ATTEMPTS = 3
SETTLE_SECONDS = 2.0


@dataclass
class CycleReport:
    status: str = "ok"
    notes: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)     # date changed, content identical
    moved: list[tuple[str, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    retried: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)     # still being written
    blocked: list[tuple[str, str]] = field(default_factory=list)
    persistent_failures: list[tuple[str, str]] = field(default_factory=list)  # -> notify user
    duplicates: list[list[str]] = field(default_factory=list)
    review_needed: list[tuple[str, str]] = field(default_factory=list)  # -> notify user
    redacted: list[tuple[str, int]] = field(default_factory=list)       # (file, how many removed)
    released: list[str] = field(default_factory=list)   # processed after the user's review decision
    results: list[ExtractionResult] = field(default_factory=list)

    @property
    def changed_anything(self) -> bool:
        return bool(self.added or self.modified or self.moved or self.removed or self.retried
                    or self.released)


class Monitor:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH,
                 state_path: Path = DEFAULT_STATE_PATH,
                 settle_seconds: float = SETTLE_SECONDS,
                 clock: Callable[[], float] = time.monotonic,
                 outbox_dir: Path = outbox.DEFAULT_OUTBOX,
                 review_path: Path = DEFAULT_REVIEW_PATH,
                 manual_path: Path = DEFAULT_MANUAL_PATH):
        self.config_path = config_path
        self.state_path = state_path
        self.settle_seconds = settle_seconds
        self.clock = clock
        self.outbox_dir = outbox_dir
        self.review_path = review_path
        self.manual_path = manual_path
        self.state, warning = load_state(state_path)
        self.startup_warning = warning
        self._pending: dict[str, tuple[Signature, float]] = {}  # in memory only

    # -- settings are re-read from disk before every file, after reading it, and right
    #    before writing to the outbox, so revoke/pause act immediately (spec §28).
    #    A PathGuard holds a COPY of the settings; it can never notice a later revoke
    #    by itself – that is why this live check exists. --
    def _current_guard(self, expected_grant: tuple[str, str | None]) -> PathGuard | None:
        config = load_config(self.config_path).config
        if config.grant_key() != expected_grant or config.paused:
            return None
        return PathGuard(config)

    def _still_allowed(self, expected_grant: tuple[str, str | None]) -> bool:
        return self._current_guard(expected_grant) is not None

    def run_once(self) -> CycleReport:
        report = CycleReport()
        if self.startup_warning:
            report.notes.append(self.startup_warning)
            self.startup_warning = None

        config = load_config(self.config_path).config
        # Q3 (option A): content collected under a revoked / replaced approval is isolated
        # first, before anything else happens in this cycle.
        isolated = outbox.quarantine_unauthorized(self.outbox_dir, config)
        if isolated:
            report.notes.append(f"{len(isolated)} outbox item(s) from an earlier approval were "
                                f"isolated and will never be transferred")
        if config.approved_folder is None:
            report.status = "no folder approved – nothing read"
            return report
        if config.paused:
            report.status = "paused – nothing read"
            return report

        guard = PathGuard(config)
        root_decision = guard.check(config.approved_folder)
        if not root_decision.allowed:
            report.status = f"approved folder unavailable: {root_decision.reason}"
            return report
        root_key = config.approved_folder
        grant = config.grant_key()
        root = Path(root_key)

        # Something was isolated -> the outbox no longer matches what we remember, so the
        # files are read again under the CURRENT approval (e.g. entries from before
        # approval tracking existed, or a damaged entry).
        if (self.state.root != root_key or self.state.grant_id != config.grant_id
                or isolated):
            if self.state.root is not None:
                report.notes.append("approved folder or approval changed – starting fresh")
            self.state = MonitorState(root=root_key, grant_id=config.grant_id)
            self._pending.clear()

        scan = scan_folder(root, guard)
        report.blocked = scan.blocked
        files = self.state.files
        now = self.clock()
        decisions = load_decisions(self.review_path)
        manual = load_manual(self.manual_path)

        # ---- 1. which files need attention? ----
        ready: list[str] = []
        for rel, sig in scan.files.items():
            record = files.get(rel)
            known = record is not None and record.status != REMOVED
            if known and (record.size, record.mtime_ns) == sig:
                self._pending.pop(rel, None)
                user_decided = record.sha256 in decisions or record.sha256 in manual
                if record.status == base.FAILED and (record.attempts < MAX_ATTEMPTS or user_decided):
                    ready.append(rel)  # automatic retry, or the user decided what to do
                elif record.status == PENDING_REVIEW and record.sha256 in decisions:
                    ready.append(rel)  # the user has just decided about this file
                continue
            first_seen = self._pending.get(rel)
            if first_seen is None or first_seen[0] != sig:
                self._pending[rel] = (sig, now)   # new change -> (re)start the settle timer
            if now - self._pending[rel][1] >= self.settle_seconds:
                ready.append(rel)
                del self._pending[rel]
            else:
                report.pending.append(rel)
        for rel in list(self._pending):
            if rel not in scan.files:
                del self._pending[rel]

        missing = {rel for rel, r in files.items()
                   if r.status != REMOVED and rel not in scan.files}
        missing_by_hash = {files[rel].sha256: rel for rel in missing}

        # ---- 2. process them, one by one ----
        stopped = False
        for rel in ready:
            current_guard = self._current_guard(grant)
            if current_guard is None:
                report.status = "stopped – permission revoked or paused during the cycle"
                stopped = True
                break
            path = root / rel
            sig = scan.files[rel]
            record = files.get(rel)
            digest = hash_file(path, current_guard)
            if digest is None:
                previous = record if record is not None and record.status == base.FAILED else None
                outbox.remove_entry(self.outbox_dir, rel)
                self._record_failure(rel, sig, "", "could not read file for fingerprint",
                                     report, previous=previous)
                continue

            # Renamed / moved inside the folder: same content as a missing file.
            if (record is None or record.status == REMOVED) and digest in missing_by_hash:
                old_rel = missing_by_hash.pop(digest)
                missing.discard(old_rel)
                moved_record = files.pop(old_rel)
                moved_record.size, moved_record.mtime_ns = sig
                files[rel] = moved_record
                outbox.move_entry(self.outbox_dir, old_rel, rel)
                report.moved.append((old_rel, rel))
                continue

            # Only the date changed, content is identical -> no reprocessing.
            if (record is not None and record.status not in (REMOVED, base.FAILED, PENDING_REVIEW)
                    and record.sha256 == digest):
                record.size, record.mtime_ns = sig
                report.touched.append(rel)
                continue

            is_retry = record is not None and record.status == base.FAILED and record.sha256 == digest
            result = extract_file(path, current_guard,
                                  still_allowed=lambda: self._still_allowed(grant))
            if not self._still_allowed(grant):
                # Revoked / paused while this file was being read: discard the result.
                report.status = "stopped – permission revoked or paused during the cycle"
                stopped = True
                break
            if result.status == base.BLOCKED:
                report.blocked.append((rel, result.error or "blocked"))
                continue
            # Spec §5: when reading fails, the user may type the content or ignore the file.
            if result.status == base.FAILED and digest in manual:
                result = ExtractionResult(result.path, base.OK, "manual",
                                          [Segment("typed by user", manual[digest])])
            elif result.status == base.FAILED and decisions.get(digest) == IGNORE:
                outbox.remove_entry(self.outbox_dir, rel)
                files[rel] = FileRecord(digest, sig[0], sig[1], IGNORED, result.error)
                report.released.append(rel)
                continue
            report.results.append(result)
            if result.status == base.FAILED:
                outbox.remove_entry(self.outbox_dir, rel)
                self._record_failure(rel, sig, digest, result.error or "read failed", report,
                                     previous=record if is_retry else None)
            else:
                try:
                    files[rel] = self._filter_and_publish(rel, digest, sig, result,
                                                          decisions.get(digest), report, grant)
                except _PermissionChanged:
                    report.results.remove(result)
                    report.status = "stopped – permission revoked or paused during the cycle"
                    stopped = True
                    break
            was_reviewed = (record is not None and record.sha256 == digest
                            and (record.status == PENDING_REVIEW
                                 or (record.status == base.FAILED and result.reader == "manual")))
            if was_reviewed:
                report.released.append(rel)
            elif is_retry:
                report.retried.append(rel)
            elif record is None or record.status == REMOVED:
                report.added.append(rel)
            else:
                report.modified.append(rel)

        # ---- 3. files that disappeared (deleted or moved out of the folder) ----
        # Spec: stop using their information, but keep history -> mark, don't erase.
        # While some file is still settling, it may be the SAME file under a new name
        # (rename = old name disappears, new name appears). So we wait with removals
        # until nothing is pending; otherwise a rename would look like delete + new.
        if not stopped and not report.pending:
            for rel in sorted(missing):
                files[rel].status = REMOVED
                outbox.remove_entry(self.outbox_dir, rel)
                report.removed.append(rel)

        # ---- 4. exact duplicates (identical content). Meaning-level duplicates = AI's job.
        groups: dict[str, list[str]] = defaultdict(list)
        for rel, r in files.items():
            # Empty files are all "identical" – reporting them as duplicates is just noise.
            if r.status != REMOVED and r.sha256 and r.size > 0:
                groups[r.sha256].append(rel)
        report.duplicates = [sorted(g) for g in groups.values() if len(g) > 1]

        save_state(self.state, self.state_path)
        return report

    def _filter_and_publish(self, rel: str, digest: str, sig: Signature,
                            result: ExtractionResult, decision: str | None,
                            report: CycleReport,
                            grant: tuple[str, str | None]) -> FileRecord:
        """Security filter -> outbox. Nothing reaches the outbox without passing the filter."""
        record = FileRecord(digest, sig[0], sig[1], result.status, result.error, len(result.segments))
        if result.status != base.OK:          # empty / needs_ocr / unsupported: nothing to send
            outbox.remove_entry(self.outbox_dir, rel)
            return record
        if decision == IGNORE:
            outbox.remove_entry(self.outbox_dir, rel)
            record.status = IGNORED
            return record

        outcome = filter_result(result, accept_uncertain=(decision == APPROVE))
        if outcome.status == PENDING_REVIEW:   # fail closed: stop this file, ask the user
            outbox.remove_entry(self.outbox_dir, rel)
            where = ", ".join(sorted({f"{u.kind} in {u.location}" for u in outcome.uncertain}))
            record.status, record.error, record.segments = PENDING_REVIEW, where, 0
            report.review_needed.append((rel, where))
            return record

        # Last live check, immediately before anything is written to the outbox.
        if not self._still_allowed(grant):
            raise _PermissionChanged()
        outbox.write_entry(self.outbox_dir, rel, digest, outcome, grant,
                           reader=result.reader, warnings=result.warnings)
        if outcome.redactions:
            report.redacted.append((rel, len(outcome.redactions)))
        return record

    def _record_failure(self, rel: str, sig: Signature, digest: str, error: str,
                        report: CycleReport, previous: FileRecord | None = None) -> None:
        attempts = (previous.attempts if previous else 0) + 1
        from collector.readers.extract import OCR_HELD
        if error and OCR_HELD in error:
            # A decision, not a passing problem: retrying cannot help – straight to the unreadable
            # list (ignore / type its content), without three useless attempts.
            attempts = max(attempts, MAX_ATTEMPTS)
        self.state.files[rel] = FileRecord(digest, sig[0], sig[1], base.FAILED, error, 0, attempts)
        if attempts >= MAX_ATTEMPTS:
            report.persistent_failures.append((rel, error))
