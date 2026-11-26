"""Automatic sending to the local brain while `watch` runs (at most every SEND_EVERY_S)."""

import unittest
from unittest import mock

from collector import main


class TestAutoSend(unittest.TestCase):
    def test_throttled_and_only_with_link(self):
        now = [0.0]
        calls = []
        cfg = mock.Mock()
        cfg.config.brain_link = {"port": 8765, "token_file": "x"}
        with mock.patch.object(main, "load_config", return_value=cfg), \
                mock.patch.object(main, "transfer_to_brain", side_effect=lambda quiet: calls.append(quiet)):
            tick = main.auto_sender(clock=lambda: now[0])
            tick(); now[0] = 100; tick(); now[0] = main.SEND_EVERY_S + 1; tick()
            self.assertEqual(calls, [True, True])
            cfg.config.brain_link = None
            now[0] = 10_000; tick()
            self.assertEqual(len(calls), 2)

    def test_watch_survives_a_failing_sender(self):
        import threading
        from collector.monitor import watcher
        stop = threading.Event()
        monitor = mock.Mock()
        monitor.run_once.return_value = mock.Mock(pending=False)
        monitor.settle_seconds = 0

        def boom():
            stop.set()
            raise ConnectionError("brain down")
        with mock.patch.object(watcher, "load_config") as lc:
            lc.return_value.config.paused, lc.return_value.config.approved_folder = True, None
            watcher.watch(monitor, lambda r: None, poll_seconds=0.01, stop=stop, after_cycle=boom)
        self.assertTrue(monitor.run_once.called)


if __name__ == "__main__":
    unittest.main()
