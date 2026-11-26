"""Tests for collector.config.

Every test uses a temporary folder, so the real settings file is never touched.
Run from the project folder (PowerShell):
    $env:PYTHONPATH = "src"; python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from collector.config import CollectorConfig, load_config, save_config


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def test_missing_file_gives_safe_default_without_warning(self):
        result = load_config(self.path)
        self.assertIsNone(result.config.approved_folder)
        self.assertIsNone(result.warning)

    def test_corrupted_json_fails_closed(self):
        self._write("{ this is not json")
        result = load_config(self.path)
        self.assertIsNone(result.config.approved_folder)
        self.assertIsNotNone(result.warning)

    def test_top_level_not_object_fails_closed(self):
        self._write('["C:/somewhere"]')
        result = load_config(self.path)
        self.assertIsNone(result.config.approved_folder)
        self.assertIsNotNone(result.warning)

    def test_wrong_type_for_folder_fails_closed(self):
        self._write(json.dumps({"approved_folder": 123}))
        result = load_config(self.path)
        self.assertIsNone(result.config.approved_folder)
        self.assertIsNotNone(result.warning)

    def test_empty_folder_string_fails_closed(self):
        self._write(json.dumps({"approved_folder": "   "}))
        result = load_config(self.path)
        self.assertIsNone(result.config.approved_folder)
        self.assertIsNotNone(result.warning)

    def test_bad_exclusions_fail_closed(self):
        self._write(json.dumps({"approved_folder": "C:/study", "exclusions": [".env", 5]}))
        result = load_config(self.path)
        self.assertIsNone(result.config.approved_folder)
        self.assertIsNotNone(result.warning)

    def test_paused_must_be_boolean(self):
        self._write(json.dumps({"approved_folder": "C:/study", "paused": "yes"}))
        result = load_config(self.path)
        self.assertIsNone(result.config.approved_folder)
        self.assertIsNotNone(result.warning)


class TestSaveConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Nested folder that does not exist yet – save must create it.
        self.path = Path(self._tmp.name) / "local_data" / "settings.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_with_hebrew_path_and_spaces(self):
        original = CollectorConfig(
            approved_folder=r"C:\Users\test\Desktop\הנדסת תוכנה\שנה ב",
            exclusions=[".env", "secrets/"],
            paused=True,
        )
        save_config(original, self.path)
        result = load_config(self.path)
        self.assertIsNone(result.warning)
        self.assertEqual(result.config, original)

    def test_save_leaves_no_temporary_file(self):
        save_config(CollectorConfig(), self.path)
        leftovers = [p.name for p in self.path.parent.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_hebrew_is_stored_readable(self):
        save_config(CollectorConfig(approved_folder="C:/לימודים"), self.path)
        from collector.security import at_rest                 # settings are encrypted at rest (§57)
        self.assertIn("לימודים", at_rest.read(self.path)["approved_folder"])


if __name__ == "__main__":
    unittest.main()


class TestPackagedLocation(unittest.TestCase):
    """Stage 8: inside a packaged .exe, settings must live NEXT TO the .exe,
    not in the temporary folder the .exe unpacks itself into."""

    def test_frozen_exe_keeps_data_next_to_the_exe(self):
        from collector.config import local_data_dir
        fake_exe = Path(tempfile.gettempdir()).resolve() / "apps" / "learnloop-collector.exe"
        self.assertEqual(local_data_dir(True, str(fake_exe)), fake_exe.parent / "local_data")

    def test_from_source_uses_project_folder(self):
        from collector.config import PROJECT_ROOT, local_data_dir
        self.assertEqual(local_data_dir(False, "python.exe"), PROJECT_ROOT / "local_data")
