"""Tests for granting / revoking / pausing. A fake home folder is used,
so the real home, Downloads and settings are never touched."""

import tempfile
import unittest
from pathlib import Path

from collector.config import load_config
from collector.permissions import folder_access as fa


class TestFolderAccess(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.home = base / "home"
        self.study = self.home / "Desktop" / "לימודים"
        self.study.mkdir(parents=True)
        (self.home / "Downloads").mkdir()
        (self.home / "AppData").mkdir()
        self.cfg = base / "local_data" / "settings.json"

    def tearDown(self):
        self._tmp.cleanup()

    def grant(self, folder):
        return fa.grant_folder(str(folder), self.cfg, home=self.home)

    def test_grant_normal_folder(self):
        result = self.grant(self.study)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(load_config(self.cfg).config.approved_folder, str(self.study))

    def test_refuse_home(self):
        self.assertFalse(self.grant(self.home).ok)

    def test_refuse_folder_containing_home(self):
        self.assertFalse(self.grant(self.home.parent).ok)

    def test_refuse_downloads(self):
        self.assertFalse(self.grant(self.home / "Downloads").ok)

    def test_refuse_inside_appdata(self):
        (self.home / "AppData" / "x").mkdir()
        self.assertFalse(self.grant(self.home / "AppData" / "x").ok)

    def test_refuse_missing_folder(self):
        self.assertFalse(self.grant(self.home / "nope").ok)

    def test_refuse_file(self):
        f = self.study / "a.txt"
        f.write_text("synthetic", encoding="utf-8")
        self.assertFalse(self.grant(f).ok)

    def test_refuse_relative_path(self):
        self.assertFalse(fa.grant_folder("study", self.cfg, home=self.home).ok)

    def test_refused_grant_does_not_change_settings(self):
        self.grant(self.study)
        self.grant(self.home / "Downloads")
        self.assertEqual(load_config(self.cfg).config.approved_folder, str(self.study))

    def test_revoke(self):
        self.grant(self.study)
        fa.revoke_folder(self.cfg)
        self.assertIsNone(load_config(self.cfg).config.approved_folder)

    def test_pause_and_resume(self):
        self.grant(self.study)
        fa.set_paused(True, self.cfg)
        self.assertTrue(load_config(self.cfg).config.paused)
        fa.set_paused(False, self.cfg)
        self.assertFalse(load_config(self.cfg).config.paused)

    def test_new_grant_keeps_exclusions_and_unpauses(self):
        fa.add_exclusion("private", self.cfg)
        fa.set_paused(True, self.cfg)
        self.grant(self.study)
        config = load_config(self.cfg).config
        self.assertEqual(config.exclusions, ["private"])
        self.assertFalse(config.paused)

    def test_exclusion_add_remove(self):
        fa.add_exclusion("*.log", self.cfg)
        fa.add_exclusion("*.log", self.cfg)   # no duplicates
        self.assertEqual(load_config(self.cfg).config.exclusions, ["*.log"])
        self.assertTrue(fa.remove_exclusion("*.log", self.cfg).ok)
        self.assertFalse(fa.remove_exclusion("*.log", self.cfg).ok)
        self.assertFalse(fa.add_exclusion("  ", self.cfg).ok)


if __name__ == "__main__":
    unittest.main()
