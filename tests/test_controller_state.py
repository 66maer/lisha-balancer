"""Regression checks without starting threads, hotkeys, or desktop windows."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QPushButton, QWidget

import lisha_overlay.app as app_module
from lisha_overlay.app import OverlayController
from lisha_overlay.panel import ControlPanel, PreviewWindow


@pytest.mark.parametrize("error_type", [OSError, ValueError, RuntimeError])
def test_invalid_region_cannot_restart_previous_capture(qt_app, monkeypatch, error_type):
    panel = ControlPanel()
    preview = PreviewWindow()
    old_region = {"left": 100, "top": 120, "width": 320, "height": 240}
    frame = QImage(320, 240, QImage.Format.Format_RGB32)
    frame.fill(0xFF102030)
    metadata = {**old_region, "region": dict(old_region), "elapsed_ms": 1.2}
    panel.set_frame_info(metadata)
    preview.update_frame(frame, metadata)
    preview_save = preview.findChild(QPushButton, "previewSaveButton")
    assert panel.save_button.isEnabled()
    assert preview_save.isEnabled()

    # Exercise the real start and validation methods, without constructing the
    # controller's worker thread or registering global shortcuts.
    controller = SimpleNamespace(
        running=False,
        locked=True,
        _quitting=False,
        _region=dict(old_region),
        _frame=frame,
        _metadata=dict(metadata),
        scan=SimpleNamespace(INSET=4),
        panel=panel,
        preview=preview,
        start_requested=SimpleNamespace(emit=Mock()),
        _exclude_windows=Mock(return_value=True),
        _set_running=Mock(),
        _cancel_tank_selection=Mock(),
        set_locked=Mock(),
    )
    controller._update_region = MethodType(OverlayController._update_region, controller)
    controller._on_capture_error = MethodType(OverlayController._on_capture_error, controller)
    monkeypatch.setattr(
        app_module,
        "native_client_region",
        Mock(side_effect=error_type("扫描框跨越显示器，无法采集。")),
    )

    try:
        OverlayController.toggle_capture(controller)

        assert not controller.running
        controller.start_requested.emit.assert_not_called()
        controller._set_running.assert_not_called()
        controller._exclude_windows.assert_not_called()
        assert controller._region == {}
        assert controller._frame.isNull()
        assert controller._metadata == {}
        assert not panel.save_button.isEnabled()
        assert not preview_save.isEnabled()
        assert preview._canvas._image.isNull()
    finally:
        panel.deleteLater()
        preview.deleteLater()


class _PanelWithNarrowSizeHint(QWidget):
    """Qt can report a narrower layout hint than a widget's fixed width."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(360, 322)

    def sizeHint(self):
        return QSize(328, 322)


def test_detached_panel_restores_fully_inside_screen_using_actual_width(qt_app):
    bounds = QRect(0, 0, 1280, 720)
    screen = SimpleNamespace(availableGeometry=lambda: QRect(bounds))
    application = SimpleNamespace(
        primaryScreen=lambda: screen,
        screenAt=lambda point: screen if bounds.contains(point) else None,
    )
    scan = QWidget()
    panel = _PanelWithNarrowSizeHint()
    controller = SimpleNamespace(
        app=application,
        settings={
            "scan_geometry": [80, 100, 360, 480],
            "panel_geometry": [1200, 40, 360, 322],
        },
        attached=False,
        scan=scan,
        panel=panel,
        _dock_panel=Mock(),
    )

    try:
        OverlayController._place_initial(controller)

        assert panel.width() == 360
        assert panel.sizeHint().width() < panel.width()
        assert bounds.contains(panel.geometry())
        controller._dock_panel.assert_not_called()
    finally:
        scan.deleteLater()
        panel.deleteLater()
