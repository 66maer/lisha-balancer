"""Local widget events only: no desktop cursor, keyboard, or native input calls."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter, QRegion
from PySide6.QtWidgets import QWidget

from lisha_overlay import tank_picker
from lisha_overlay.tank_picker import TankPicker
from lisha_overlay.team_layout import TeamLayout


@pytest.fixture
def picker(qt_app, monkeypatch):
    monkeypatch.setattr(tank_picker, "exclude_from_capture", lambda widget: True)
    monkeypatch.setattr(tank_picker, "set_click_through", lambda widget, enabled: True)
    widget = TankPicker()
    widget.set_grid(TeamLayout.uniform(right=.08), QRect(-700, 100, 381, 513))
    widget.set_current_tank("g1p1")
    yield widget
    widget.close()
    widget.deleteLater()


def mouse_press(widget, point, button):
    position = QPointF(point)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, position,
                        QPointF(widget.mapToGlobal(position.toPoint())),
                        button, button, Qt.KeyboardModifier.NoModifier)
    widget.mousePressEvent(event)


def render(widget):
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        widget.render(painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    finally:
        painter.end()
    return image


def test_picker_uses_calibrated_cells_and_hides_before_reporting_selection(picker):
    cells = {cell.key: cell for cell in picker.team_layout.cells(picker.content_rect())}
    position = cells["g5p2"].rect.center()
    assert picker.geometry() == QRect(-700, 100, 381, 513)
    assert picker.cell_at(position) == "g5p2"
    assert picker.cell_at(QPointF(1, 1)) is None
    selected, cancelled = [], []
    picker.tank_selected.connect(lambda key: selected.append((key, picker.isVisible(), picker.is_selecting)))
    picker.cancelled.connect(lambda: cancelled.append(True))

    assert picker.start_selection()
    assert picker.excluded
    assert not picker.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    image = render(picker)
    assert image.pixelColor(1, 1).alpha() <= 1
    assert any(image.pixelColor(x, y).alpha() > 40
               for x in range(image.width()) for y in range(image.height()))

    mouse_press(picker, position, Qt.MouseButton.LeftButton)
    assert selected == [("g5p2", False, False)]
    assert not cancelled
    assert picker.current_tank == "g5p2"
    assert picker.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    cleared = render(picker)
    assert not any(bytes(cleared.constBits()))


@pytest.mark.parametrize("action", ["right", "escape", "outside_cell", "deactivate", "hide"])
def test_cancel_paths_release_the_selection_surface_once(picker, action):
    selected, cancelled = [], []
    picker.tank_selected.connect(selected.append)
    picker.cancelled.connect(lambda: cancelled.append(True))
    assert picker.start_selection()
    if action == "right":
        mouse_press(picker, QPointF(20, 30), Qt.MouseButton.RightButton)
    elif action == "escape":
        picker.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    elif action == "outside_cell":
        mouse_press(picker, QPointF(1, 1), Qt.MouseButton.LeftButton)
    elif action == "deactivate":
        picker.event(QEvent(QEvent.Type.WindowDeactivate))
    else:
        picker.hide()
    picker.cancel_selection()
    assert cancelled == [True]
    assert not selected
    assert not picker.isVisible() and not picker.is_selecting
    assert picker.current_tank == "g1p1"
    assert picker.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_failed_capture_exclusion_does_not_leave_an_input_overlay(picker, monkeypatch):
    monkeypatch.setattr(tank_picker, "exclude_from_capture", lambda widget: False)
    cancelled = []
    picker.cancelled.connect(lambda: cancelled.append(True))
    assert not picker.start_selection()
    assert cancelled == [True]
    assert not picker.isVisible() and not picker.is_selecting


def test_invalid_grid_cannot_start_a_selection(qt_app):
    picker = TankPicker()
    try:
        with pytest.raises(ValueError):
            picker.start_selection()
        with pytest.raises(ValueError):
            picker.set_grid(TeamLayout(), QRect(0, 0, 8, 8))
        with pytest.raises(ValueError):
            picker.set_grid(TeamLayout(), QRect(0, 0, 400, 500), inset=-1)
        assert not picker.isVisible()
    finally:
        picker.deleteLater()
