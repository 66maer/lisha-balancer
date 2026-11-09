from PySide6.QtCore import QPoint, QRect

from lisha_overlay.app import _read_rect, _setting_float
from lisha_overlay.overlay import bounded_rect, resized_rect


def test_move_to_negative_monitor_preserves_size():
    bounds = QRect(-1920, 0, 1920, 1080)
    assert bounded_rect(QRect(-10, 900, 360, 480), bounds) == QRect(-360, 600, 360, 480)


def test_topleft_resize_anchors_other_corner_and_minimum():
    start = QRect(200, 200, 360, 480)
    actual = resized_rect(start, QPoint(600, 600), "lt", QRect(0, 0, 1920, 1080))
    assert actual == QRect(440, 560, 120, 120)
    assert actual.bottomRight() == start.bottomRight()


def test_resize_cannot_leave_monitor():
    start = QRect(-800, 200, 360, 480)
    bounds = QRect(-1920, 0, 1920, 1080)
    assert resized_rect(start, QPoint(1000, 1000), "rb", bounds) == QRect(-800, 200, 800, 880)


def test_corrupt_saved_geometry_does_not_make_windows_unrecoverable():
    assert _read_rect([0, 0, -2, 400]) is None
    assert _read_rect([True, 2, 300, 400]) is None
    assert _read_rect([10000000000, 0, 300, 400]) is None
    assert _read_rect([0, 0, 300, 400]) == QRect(0, 0, 300, 400)
    assert _setting_float({"opacity": float("nan")}, "opacity", 0.9) == 0.9
    assert _setting_float({"opacity": 0}, "opacity", 0.9) == 0.2
