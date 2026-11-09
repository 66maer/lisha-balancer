"""Regression checks for negative origins and mixed-DPI monitor islands."""

import unittest

from PySide6.QtCore import QRect

from lisha_overlay.capture import normalize_region
from lisha_overlay.platform_win import _map_rect_to_monitor, _matching_monitor


class PhysicalCoordinatesTests(unittest.TestCase):
    def test_friendly_monitor_names_match_geometry_instead_of_enumeration_order(self):
        left = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
        right = {"left": 0, "top": 0, "width": 2560, "height": 1440}
        monitors = {"display2": right, "display1": left}
        self.assertIs(_matching_monitor("ROG PG279Q (1)", QRect(-1920, 0, 1280, 720), 1.5, monitors), left)
        self.assertIs(_matching_monitor("ROG PG279Q (2)", QRect(0, 0, 2560, 1440), 1., monitors), right)
        self.assertIsNone(_matching_monitor("ROG PG279Q", QRect(100, 0, 1280, 720), 1.5, monitors))

    def test_ambiguous_monitor_identity_is_not_guessed(self):
        region = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        self.assertIsNone(_matching_monitor("friendly", QRect(0, 0, 1920, 1080), 1.,
                                           {"display1": region, "display2": dict(region)}))

    def test_left_monitor_origin_is_not_scaled(self):
        region = _map_rect_to_monitor(
            QRect(-1820, 50, 200, 100),
            QRect(-1920, 0, 1280, 720),
            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
            1.5,
        )
        self.assertEqual(region, {"left": -1770, "top": 75, "width": 300, "height": 150})

    def test_monitor_above_primary_preserves_negative_y(self):
        region = _map_rect_to_monitor(
            QRect(30, -1150, 400, 200),
            QRect(0, -1200, 1920, 960),
            {"left": 0, "top": -1200, "width": 2400, "height": 1200},
            1.25,
        )
        self.assertEqual(region["top"], -1138)
        self.assertEqual(region["height"], 250)

    def test_selection_across_monitor_boundary_is_rejected(self):
        with self.assertRaises(ValueError):
            _map_rect_to_monitor(
                QRect(-100, 50, 200, 100),
                QRect(-1920, 0, 1920, 1080),
                {"left": -1920, "top": 0, "width": 1920, "height": 1080},
                1,
            )

    def test_capture_accepts_negative_origin_and_rejects_invalid_size(self):
        expected = {"left": -100, "top": -200, "width": 50, "height": 60}
        self.assertEqual(normalize_region(expected), expected)
        for invalid in (
            {**expected, "width": 0},
            {**expected, "left": 1.25},
            {**expected, "width": True},
        ):
            with self.assertRaises(ValueError):
                normalize_region(invalid)
