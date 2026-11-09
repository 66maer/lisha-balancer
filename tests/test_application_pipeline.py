"""Opt-in real-image replay through the actual app/worker path, offscreen only."""
import json
import os

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage

from lisha_overlay import app as app_module, overlay, platform_win


@pytest.mark.parametrize("continuous", [False, True] * 3)
def test_real_image_passes_worker_confirmation_and_marks_without_desktop_input(qt_app, monkeypatch, tmp_path, continuous):
    filename = os.environ.get("LISHA_REFERENCE_IMAGE")
    if not filename:
        pytest.skip("Private reference replay is opt-in.")
    frame = QImage(filename)
    assert not frame.isNull()
    region = {"left": 10, "top": 10, "width": frame.width(), "height": frame.height()}
    monkeypatch.setattr(app_module, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(app_module, "load_settings", lambda: {"tank_key": "g1p1", "remote_compatible": False})
    monkeypatch.setattr(app_module, "save_settings", lambda settings: None)
    monkeypatch.setattr(app_module, "native_client_region", lambda *args: dict(region))
    monkeypatch.setattr(app_module, "register_hotkey", lambda *args: False)
    monkeypatch.setattr(app_module, "unregister_hotkey", lambda *args: True)
    for module in (app_module, overlay, platform_win):
        monkeypatch.setattr(module, "exclude_from_capture", lambda *args: True)
        monkeypatch.setattr(module, "set_click_through", lambda *args: True)
    monkeypatch.setattr(app_module, "set_topmost", lambda *args: True)
    monkeypatch.setattr(platform_win, "set_topmost", lambda *args: True)
    controller = app_module.OverlayController(qt_app)
    loop = QEventLoop()
    metadata = {**region, "region": dict(region), "elapsed_ms": 1, "captured_at": 1}
    completions = []
    poll = QTimer()
    poll.setInterval(20)

    def poll_result():
        if controller._last_result is not None:
            loop.quit()
        elif controller._analysis_pending and not controller._processing_busy:
            completions.append(1)
            controller._on_frame(frame, metadata)

    poll.timeout.connect(poll_result)
    try:
        controller._update_region()
        controller.running = True
        if continuous:
            controller.grid_ready = True
            controller.analyze_mode("all")
        else:
            controller._analysis_pending = True
        completions.append(1)
        controller._on_frame(frame, metadata)
        poll.start()
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        assert len(completions) >= 2
        assert controller._last_result is not None
        assert controller._last_result.status == "solved", controller.panel._summary_text
        assert controller._last_result.difference <= 2
        assert sum(p.alive and p.color == "red" for p in controller._players) == 10
        assert sum(p.alive and p.color == "black" for p in controller._players) == 9
        assert len(controller.scan.markers.displayed_annotations()) >= 3
        assert not controller.panel.geometry().intersects(controller.scan.geometry())
        diagnostic = json.loads((tmp_path / "latest-analysis.json").read_text(encoding="utf-8"))
        assert diagnostic["status"] == "solved"
        assert len(diagnostic["players"]) == 25
        if continuous:
            assert diagnostic["published"] is True and diagnostic["stable_frames"] >= 2
            controller.stop_analysis()
            assert not controller._continuous_analysis
            assert not controller.scan.markers.displayed_annotations()
    finally:
        poll.stop()
        controller.shutdown()
        controller.deleteLater()
