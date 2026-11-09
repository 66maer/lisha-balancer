"""Diagnostic images are opt-in while structured failure records remain available."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage

from lisha_overlay import app as application
from lisha_overlay.analysis import AnalysisResult, PlayerState
from lisha_overlay.team_layout import TeamLayout


@pytest.mark.parametrize("debug_captures", [False, True])
def test_unknown_player_reason_is_saved_and_images_are_opt_in(tmp_path, monkeypatch, caplog, debug_captures):
    monkeypatch.setattr(application, "settings_path", lambda: tmp_path / "settings.json")
    frame = QImage(375, 505, QImage.Format.Format_RGB32)
    frame.fill(0xFF25394B)
    controller = SimpleNamespace(analysis_mode="all", _region={"left": 1, "top": 2, "width": 375, "height": 505},
                                 tank_key=None, snapshot_stage="before_tank", debug_captures=debug_captures,
                                 _analysis_frame=frame, team_layout=TeamLayout())
    players = [PlayerState(f"g{i//5+1}p{i%5+1}", "unknown" if i == 15 else "none",
                           None if i == 15 else 0) for i in range(25)]
    application.OverlayController._record_analysis(controller, AnalysisResult("Buff待识别 · 1人"), players, {
        "unknown_keys": ["g4p1"], "diagnostics": {"g4p1": {
            "source": "图标被倒计时遮挡", "color_confidence": .792, "stack_confidence": 0}}})
    assert "4队1" in caplog.text and "图标被倒计时遮挡" in caplog.text
    state = json.loads((tmp_path / "latest-analysis.json").read_text(encoding="utf-8"))
    if debug_captures:
        crop = QImage(state["debug_crop_files"]["g4p1"])
        assert not crop.isNull()
        assert crop.width() < 60 and crop.height() < 35
    else:
        assert "debug_crop_files" not in state
        assert not list(tmp_path.rglob("*.png"))


@pytest.mark.parametrize("debug_captures", [False, True])
def test_grid_and_failure_images_are_opt_in(tmp_path, monkeypatch, debug_captures):
    monkeypatch.setattr(application, "settings_path", lambda: tmp_path / "settings.json")
    frame = QImage(375, 505, QImage.Format.Format_RGB32)
    frame.fill(0xFF25394B)
    controller = SimpleNamespace(
        _region={}, team_layout=TeamLayout(), tank_key=None, debug_captures=debug_captures,
        _analysis_frame=frame, _quitting=False, _generation=1, test_enabled=False,
        scan=Mock(), panel=Mock(),
    )
    application.OverlayController._record_grid(controller, "not_found", {"debug_frame": frame})
    application.OverlayController._analysis_failed(controller, "synthetic failure", {"generation": 1})
    assert (tmp_path / "latest-grid.json").is_file()
    assert (tmp_path / "latest-failure.json").is_file()
    assert (tmp_path / "latest-grid-input.png").is_file() is debug_captures
    assert (tmp_path / "latest-failed-input.png").is_file() is debug_captures
