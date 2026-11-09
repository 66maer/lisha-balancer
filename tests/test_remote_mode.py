"""Remote mode shows windows at rest and takes only clean, on-demand frames."""
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from lisha_overlay import app as application, platform_win, overlay, tank_picker
from lisha_overlay.team_layout import Annotation


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(15, loop.quit)
        loop.exec()
    assert predicate()


@pytest.fixture
def remote_controller(qt_app, monkeypatch, tmp_path):
    monkeypatch.setattr(application, "load_settings", lambda: {"remote_compatible": True, "grid_ready": True})
    monkeypatch.setattr(application, "save_settings", lambda value: None)
    monkeypatch.setattr(application, "settings_path", lambda: tmp_path / "settings.json")
    region = {"left": 20, "top": 30, "width": 320, "height": 400}
    monkeypatch.setattr(application, "native_client_region", lambda *args: dict(region))
    monkeypatch.setattr(application, "register_hotkey", lambda *args: False)
    monkeypatch.setattr(application, "unregister_hotkey", lambda *args: True)
    for module in (application, platform_win, overlay, tank_picker):
        for name in ("exclude_from_capture", "set_click_through", "set_topmost"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda *args: True)
    import mss
    captures = []
    class Capture:
        monitors = [{}, {"left": 0, "top": 0, "width": 3000, "height": 2000}]
        def __init__(self):
            self.closed = False
            captures.append(self)
        def grab(self, value):
            assert not self.closed
            return SimpleNamespace(width=value["width"], height=value["height"],
                                   bgra=b"\x10\x20\x30\x00" * value["width"] * value["height"])
        def close(self):
            self.closed = True
    monkeypatch.setattr(mss, "mss", Capture)
    controller = application.OverlayController(qt_app)
    yield controller, captures
    controller.shutdown()
    controller.deleteLater()
    platform_win.set_remote_compatible(False)


def test_remote_frame_is_one_shot_and_layers_return_without_a_continuous_timer(remote_controller):
    controller, captures = remote_controller
    controller._update_region()
    controller.scan.set_annotations([Annotation('g1p1', 'fan')])
    controller.toggle_capture()
    assert controller.scan.isVisible() and controller.panel.isVisible()
    assert not controller.scan.markers.isVisible()
    wait_until(lambda: not controller._frame.isNull())
    assert controller.scan.isVisible() and controller.scan.markers.isVisible()
    assert controller._remote_capture_kind is None
    assert controller._metadata["remote_capture_id"] == 1
    assert controller._metadata['hidden_layers'] == ['marksOverlay']
    loop = QEventLoop()
    QTimer.singleShot(450, loop.quit)
    loop.exec()
    assert len(captures) == 1 and captures[0].closed
    assert controller.worker._timer is None


def test_pause_during_pending_snapshot_restores_hidden_windows(remote_controller):
    controller, _ = remote_controller
    controller.toggle_capture()
    controller.toggle_capture()
    assert not controller.running and controller.scan.isVisible()
    assert controller._remote_capture_kind is None


def test_capture_failure_restores_windows_and_stops_the_capture(remote_controller, monkeypatch):
    controller, _ = remote_controller
    import mss
    class Broken:
        def __init__(self):
            raise OSError("screen unavailable")
    monkeypatch.setattr(mss, "mss", Broken)
    controller.toggle_capture()
    wait_until(lambda: not controller.running)
    assert controller.scan.isVisible() and controller.panel.isVisible()
    assert controller._remote_capture_kind is None


def test_affinity_is_cleared_for_remote_windows_and_restored_for_local_mode(monkeypatch):
    api = SimpleNamespace(SetWindowDisplayAffinity=Mock(return_value=True))
    monkeypatch.setattr(platform_win, "_IS_WINDOWS", True)
    monkeypatch.setattr(platform_win, "_USER32", api)
    widget = SimpleNamespace(winId=lambda: 123)
    try:
        platform_win.set_remote_compatible(True)
        assert platform_win.exclude_from_capture(widget)
        api.SetWindowDisplayAffinity.assert_called_with(123, 0)
        platform_win.set_remote_compatible(False)
        assert platform_win.exclude_from_capture(widget)
        api.SetWindowDisplayAffinity.assert_called_with(123, 0x11)
    finally:
        platform_win.set_remote_compatible(False)


def test_pinned_windows_are_refreshed_by_timer_and_unpin_stops_it(remote_controller, monkeypatch):
    controller, _ = remote_controller
    refresh = Mock(return_value=True)
    monkeypatch.setattr(application, "set_topmost", refresh)
    assert controller._topmost_timer.isActive()
    wait_until(lambda: refresh.call_count >= 3)
    assert [call.args[0] for call in refresh.call_args_list[:3]] == [
        controller.scan, controller.scan.markers, controller.panel,
    ]
    controller.set_pinned(False)
    assert not controller._topmost_timer.isActive()
    refresh.reset_mock()
    controller._refresh_topmost()
    refresh.assert_not_called()
    controller.set_pinned(True)
    assert controller._topmost_timer.isActive()


def test_topmost_refresh_keeps_capture_layers_hidden_and_menus_above_panel(remote_controller, monkeypatch):
    controller, _ = remote_controller
    refresh = Mock(return_value=True)
    monkeypatch.setattr(application, "set_topmost", refresh)
    dialog = SimpleNamespace(isVisible=lambda: True, isMinimized=lambda: False)
    popup = SimpleNamespace(isVisible=lambda: True, isMinimized=lambda: False)
    monkeypatch.setattr(controller.app, "activeModalWidget", lambda: dialog)
    monkeypatch.setattr(controller.app, "activePopupWidget", lambda: popup)
    controller.scan.markers.hide()
    controller._refresh_topmost()
    assert [call.args[0] for call in refresh.call_args_list] == [
        controller.scan, controller.panel, dialog, popup,
    ]
    assert not controller.scan.markers.isVisible()
    refresh.reset_mock()
    controller._hidden = True
    controller._refresh_topmost()
    refresh.assert_not_called()
    controller._hidden = False
    controller._quitting = True
    controller._refresh_topmost()
    refresh.assert_not_called()
    controller._quitting = False
