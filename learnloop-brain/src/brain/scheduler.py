"""When AI processing runs (spec §35, APPROVED).

  - Routine processing every TWO HOURS while the computer is running.
  - Early triggers: a significant accumulation of material, or an explicit user request
    (the "process now" button / POST /api/cycle – that path runs immediately, not here).
  - Not every small file change starts the AI.
  - If the user is studying right now: do not interrupt; keep the event in history; process
    at a suitable time (after she finishes or has been idle for a while). A short,
    non-intrusive notice may be shown.

Numbers that the spec does not give are settings marked ASSUMPTION (settings.py).
The scheduler only DECIDES and calls app.run_cycle(); it holds no learning logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from brain.i18n import msg


class CycleScheduler:
    def __init__(self, app, settings=None):
        self.app = app
        self.settings = (settings or app.settings).schedule
        self.deferred_since: str | None = None

    # ------------------------------------------------------------------ decision
    def pending(self) -> tuple[int, int]:
        inbox = self.app.ingestion.inbox
        return len(inbox), sum(len(c["text"]) for e in inbox for c in e["chunks"])

    def studying_now(self, now: datetime) -> bool:
        """An activity in progress with an interaction in the last `quiet_minutes`."""
        quiet = timedelta(minutes=self.settings.quiet_minutes)
        for a in self.app.repo.activities.values():
            if a.status != "in_progress":
                continue
            times = [a.started_at] + [e.get("answered_at") for e in a.answers.values()]
            last = max(datetime.fromisoformat(t) for t in times if t)
            if now - last < quiet:
                return True
        return False

    def decide(self, now: datetime) -> str:
        files, chars = self.pending()
        if files == 0:
            return "idle"
        last = self.app.last_cycle_at
        due = last is None or now - datetime.fromisoformat(last) >= timedelta(hours=self.settings.interval_hours)
        accumulated = files >= self.settings.accumulation_files or chars >= self.settings.accumulation_chars
        if not (due or accumulated):
            return "idle"
        if self.studying_now(now):
            return "deferred_studying"
        return "scheduled" if due else "accumulation"

    # ------------------------------------------------------------------ action
    def tick(self, now: datetime | None = None) -> dict:
        now = now or datetime.fromisoformat(self.app.repo.clock())
        decision = self.decide(now)
        if decision == "deferred_studying":
            if self.deferred_since is None:              # recorded once per deferral, not every tick
                self.deferred_since = now.isoformat()
                self.app.repo.record("cycle", "scheduled", "deferred", None, {"pending": self.pending()[0]},
                                     msg("h.cycle_deferred"), "autonomous")
            return {"decision": decision, "notice_key": "notice.cycle_later"}
        if decision in ("scheduled", "accumulation"):
            self.deferred_since = None
            result = self.app.run_cycle(trigger=decision)
            self.app.commit()
            return {"decision": decision, "result": result}
        return {"decision": decision}

    def status(self, now: datetime | None = None) -> dict:
        now = now or datetime.fromisoformat(self.app.repo.clock())
        files, _chars = self.pending()
        last = self.app.last_cycle_at
        nxt = None if last is None else (datetime.fromisoformat(last)
                                         + timedelta(hours=self.settings.interval_hours)).isoformat()
        return {"pending_files": files, "last_cycle_at": last, "next_cycle_at": nxt,
                "deferred_because_studying": self.deferred_since is not None,
                "interval_hours": self.settings.interval_hours}
