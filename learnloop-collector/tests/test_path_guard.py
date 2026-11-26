"""Tests for PathGuard – every escape attempt must be blocked.

All files are synthetic and created in temporary folders.
"""

import os
import tempfile
import unittest
from pathlib import Path

from collector.config import CollectorConfig
from collector.permissions.path_guard import PathGuard


class TestPathGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "study"
        self.outside = base / "outside"
        self.sibling = base / "study2"          # name starts like the root!
        for d in (self.root, self.outside, self.sibling):
            d.mkdir()
        (self.root / "lecture1.txt").write_text("synthetic", encoding="utf-8")
        hebrew = self.root / "הנדסת תוכנה" / "שנה ב"
        hebrew.mkdir(parents=True)
        (hebrew / "סיכום 1.txt").write_text("synthetic", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1", encoding="utf-8")
        (self.root / "private").mkdir()
        (self.root / "private" / "notes.txt").write_text("synthetic", encoding="utf-8")
        (self.root / "log.log").write_text("synthetic", encoding="utf-8")
        (self.outside / "secret.txt").write_text("synthetic", encoding="utf-8")
        (self.sibling / "other.txt").write_text("synthetic", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def guard(self, **kwargs):
        return PathGuard(CollectorConfig(approved_folder=str(self.root), **kwargs))

    # --- permission state ---
    def test_no_folder_approved_blocks_everything(self):
        decision = PathGuard(CollectorConfig()).check(self.root / "lecture1.txt")
        self.assertFalse(decision.allowed)

    def test_paused_blocks_everything(self):
        self.assertFalse(self.guard(paused=True).check(self.root / "lecture1.txt").allowed)

    def test_deleted_root_blocks(self):
        guard = PathGuard(CollectorConfig(approved_folder=str(self.root / "gone")))
        self.assertFalse(guard.check("lecture1.txt").allowed)

    # --- allowed paths ---
    def test_file_inside_is_allowed(self):
        self.assertTrue(self.guard().check(self.root / "lecture1.txt").allowed)

    def test_relative_path_inside_is_allowed(self):
        self.assertTrue(self.guard().check("lecture1.txt").allowed)

    def test_hebrew_path_with_spaces_is_allowed(self):
        path = self.root / "הנדסת תוכנה" / "שנה ב" / "סיכום 1.txt"
        self.assertTrue(self.guard().check(path).allowed)

    # --- escape attempts ---
    def test_dot_dot_escape_is_blocked(self):
        self.assertFalse(self.guard().check("../outside/secret.txt").allowed)

    def test_dot_dot_hidden_in_middle_is_blocked(self):
        self.assertFalse(self.guard().check("private/../../outside/secret.txt").allowed)

    def test_absolute_path_outside_is_blocked(self):
        self.assertFalse(self.guard().check(self.outside / "secret.txt").allowed)

    def test_sibling_with_similar_name_is_blocked(self):
        self.assertFalse(self.guard().check(self.sibling / "other.txt").allowed)

    def test_missing_file_is_blocked(self):
        self.assertFalse(self.guard().check("nothing_here.txt").allowed)

    # --- exclusions ---
    def test_builtin_env_exclusion(self):
        decision = self.guard().check(".env")
        self.assertFalse(decision.allowed)
        self.assertIn(".env", decision.reason)

    def test_user_folder_exclusion(self):
        self.assertFalse(self.guard(exclusions=["private"]).check("private/notes.txt").allowed)

    def test_user_pattern_exclusion_ignores_case(self):
        self.assertFalse(self.guard(exclusions=["*.LOG"]).check("log.log").allowed)

    def test_exclusion_checked_even_if_file_missing(self):
        # Excluded paths are refused before any disk access.
        self.assertIn("excluded", self.guard().check("keys/server.key").reason)

    # --- links ---
    def _symlink_or_skip(self, target: Path, link: Path):
        try:
            os.symlink(target, link, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError):
            self.skipTest("creating symbolic links is not permitted here")

    def test_symlink_pointing_outside_is_blocked(self):
        link = self.root / "shortcut"
        self._symlink_or_skip(self.outside, link)
        self.assertFalse(self.guard().check("shortcut/secret.txt").allowed)

    def test_symlink_pointing_inside_is_also_blocked(self):
        link = self.root / "alias.txt"
        self._symlink_or_skip(self.root / "lecture1.txt", link)
        self.assertFalse(self.guard().check("alias.txt").allowed)


if __name__ == "__main__":
    unittest.main()
