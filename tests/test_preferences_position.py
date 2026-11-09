"""Coordinate editing uses logical pixels and preserves negative monitor origins."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect

from lisha_overlay.preferences import PreferencesDialog


def screen(name, geometry, available=None):
    available = available or geometry
    return SimpleNamespace(
        name=lambda: name,
        geometry=lambda: QRect(geometry),
        availableGeometry=lambda: QRect(available),
        devicePixelRatio=lambda: 1.0,
    )


def scan_on(display, x, y):
    return SimpleNamespace(
        screen=lambda: display,
        x=lambda: x,
        y=lambda: y,
        width=lambda: 360,
        height=lambda: 480,
    )


def test_coordinate_controls_preserve_initial_position_and_allow_single_pixel_steps(qt_app):
    display = screen("LEFT", QRect(-1920, 0, 1920, 1080))
    dialog = PreferencesDialog([display], scan_on(display, -1750, 123), 0.96, 0.92, False)
    try:
        assert dialog.x_box.objectName() == "scanXSpin"
        assert dialog.y_box.objectName() == "scanYSpin"
        assert dialog.x_box.value() == -1750
        assert dialog.y_box.value() == 123
        for box in (dialog.x_box, dialog.y_box):
            assert (box.minimum(), box.maximum(), box.singleStep()) == (-100000, 100000, 1)
        dialog.x_box.stepUp()
        dialog.y_box.stepDown()
        assert (dialog.x_box.value(), dialog.y_box.value()) == (-1749, 122)
        assert (dialog.width_box.value(), dialog.height_box.value()) == (360, 480)
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("geometry,available,expected", [
    (QRect(-1920, 0, 1920, 1080), QRect(-1920, 40, 1920, 1040), (-1860, 120)),
    (QRect(0, -1440, 2560, 1440), QRect(0, -1440, 2560, 1400), (60, -1360)),
])
def test_switching_monitors_moves_position_inside_the_new_available_area(qt_app, geometry, available, expected):
    primary = screen("PRIMARY", QRect(0, 0, 2560, 1440))
    other = screen("OTHER", geometry, available)
    dialog = PreferencesDialog([primary, other], scan_on(primary, 125, 130), 0.96, 0.92, False)
    try:
        assert (dialog.x_box.value(), dialog.y_box.value()) == (125, 130)
        dialog.screen_box.setCurrentIndex(1)
        assert (dialog.x_box.value(), dialog.y_box.value()) == expected
        assert (dialog.width_box.value(), dialog.height_box.value()) == (360, 480)
    finally:
        dialog.deleteLater()
