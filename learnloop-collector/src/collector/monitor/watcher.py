"""Continuous mode: run Monitor cycles when Windows reports a change.

watchdog only WAKES the Monitor up. It never decides anything: every cycle
still re-scans through PathGuard, so paths reported in events are not trusted.
A periodic scan runs as a backup in case an event is lost.
If watchdog is not installed, the Monitor falls back to periodic scanning only.
"""

from __future__ import annotations

import threading
from typing import Callable

from collector.config import load_config
from collector.monitor.monitor import CycleReport, Monitor

POLL_SECONDS = 30.0


def watch(monitor: Monitor, on_report: Callable[[CycleReport], None],
          poll_seconds: float = POLL_SECONDS, stop: threading.Event | None = None,
          after_cycle: Callable[[], None] | None = None) -> None:
    stop = stop or threading.Event()
    wake = threading.Event()

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        Observer = None
        print("watchdog is not installed – using periodic scanning only.")

    observer, watched_root = None, None

    def ensure_observer() -> None:
        nonlocal observer, watched_root
        config = load_config(monitor.config_path).config
        root = None if config.paused else config.approved_folder
        if Observer is None or root == watched_root:
            return
        if observer is not None:       # folder changed or revoked -> stop watching old one
            observer.stop()
            observer.join()
            observer = None
        watched_root = root
        if root is not None:
            class WakeHandler(FileSystemEventHandler):
                def on_any_event(self, event):
                    wake.set()
            observer = Observer()
            observer.schedule(WakeHandler(), root, recursive=True)
            observer.start()

    try:
        while not stop.is_set():
            ensure_observer()
            report = monitor.run_once()
            on_report(report)
            if after_cycle is not None:
                try:
                    after_cycle()              # e.g. automatic sending to the local brain
                except Exception as e:         # never stop watching because of it
                    print(f"automatic sending skipped ({type(e).__name__})")
            # Files still being written -> check again soon; otherwise wait for an event.
            timeout = monitor.settle_seconds + 0.5 if report.pending else poll_seconds
            wake.wait(timeout)
            wake.clear()
    finally:
        if observer is not None:
            observer.stop()
            observer.join()
