import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lisha_overlay.settings import load_settings, save_settings


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "LishaBalancer" / "settings.json"
        mocked_path = patch("lisha_overlay.settings.settings_path", return_value=self.path)
        mocked_path.start()
        self.addCleanup(mocked_path.stop)

    def test_round_trip_keeps_negative_coordinates_and_unicode(self):
        settings = {"scan_geometry": [-900, -25, 600, 500], "title": "戾煞配平"}
        save_settings(settings)
        self.assertEqual(load_settings(), settings)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_missing_and_corrupt_settings_fall_back(self):
        self.assertEqual(load_settings(), {})
        self.path.parent.mkdir(parents=True)
        for content in ("{broken", "[1, 2, 3]", "null"):
            self.path.write_text(content, encoding="utf-8")
            self.assertEqual(load_settings(), {})

    def test_invalid_new_settings_do_not_destroy_previous_file(self):
        save_settings({"panel_opacity": 0.8})
        with self.assertRaises(ValueError):
            save_settings({"panel_opacity": float("nan")})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"panel_opacity": 0.8})

    def test_failed_replace_preserves_previous_file_and_removes_temporary(self):
        save_settings({"attached": True})
        with patch("lisha_overlay.settings.os.replace", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                save_settings({"attached": False})
        self.assertEqual(load_settings(), {"attached": True})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
