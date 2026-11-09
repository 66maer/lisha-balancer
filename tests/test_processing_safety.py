from types import SimpleNamespace
from unittest.mock import Mock

from lisha_overlay.analysis import AnalysisResult, PlayerState
from lisha_overlay.app import OverlayController


def roster(red_count=12, black_count=12):
    players = []
    for index in range(25):
        color = "red" if index < red_count else "black" if index < red_count + black_count else "none"
        players.append(PlayerState(f"g{index // 5 + 1}p{index % 5 + 1}", color,
                                   1 if color != "none" else 0, is_tank=index == 0))
    return players


def fake_controller():
    controller = SimpleNamespace(
        _processing_busy=True, _quitting=False, test_enabled=False, _generation=5,
        _players=[], _analysis_metadata={}, _analysis_attempts=0,
        _recognition_previous=None, _analysis_pending=False,
        isolation_confirmed=True, manual_overrides={"g1p1": {"isolated": False}},
        scan=SimpleNamespace(set_annotations=Mock()),
        panel=SimpleNamespace(set_summary=Mock(), set_status=Mock()),
        apply_analysis=Mock(),
        set_locked=Mock(),
        _record_analysis=Mock(),
    )
    controller._complete_learned_snapshot = lambda *args: OverlayController._complete_learned_snapshot(controller, *args)
    return controller


def test_real_marks_wait_for_two_matching_snapshots_and_one_shot_overrides_expire():
    controller = fake_controller()
    players = roster()
    result = AnalysisResult("红1 12 · 黑1 12", status="solved")
    OverlayController._analysis_completed(controller, result, players, {"generation": 5})
    controller.apply_analysis.assert_not_called()
    assert controller._analysis_pending
    controller._analysis_pending = False
    OverlayController._analysis_completed(controller, result, players, {"generation": 5})
    controller.apply_analysis.assert_called_once_with(result)
    assert not controller.isolation_confirmed and not controller.manual_overrides


def test_old_generation_cannot_draw_and_new_generation_resets_confirmation():
    controller = fake_controller()
    controller._analysis_attempts = 3
    controller._recognition_previous = ("old",)
    OverlayController._invalidate_analysis(controller, preserve_pending=True)
    assert controller._generation == 6 and controller._analysis_attempts == 0
    assert controller._recognition_previous is None
    OverlayController._analysis_completed(controller, AnalysisResult("旧结果", status="solved"),
                                           roster(), {"generation": 5})
    controller.apply_analysis.assert_not_called()
    assert controller._analysis_pending and not controller._processing_busy


def test_explicit_stop_rejects_late_worker_result():
    controller = fake_controller()
    controller._continuous_analysis = True
    controller._stable_frames = 3
    controller.analysis_mode = "all"
    controller._invalidate_analysis = lambda: OverlayController._invalidate_analysis(controller)
    controller.panel.set_analysis_active = Mock()
    OverlayController.stop_analysis(controller)
    assert not controller._continuous_analysis and not controller._analysis_pending
    controller.panel.set_analysis_active.assert_called_with(False)
    controller.scan.set_annotations.assert_called_with([])
    OverlayController._analysis_completed(controller, AnalysisResult("迟到方案", status="solved"),
                                           roster(), {"generation": 5})
    controller.apply_analysis.assert_not_called()


def test_worker_failure_resets_learned_confirmation_before_retry():
    controller = fake_controller()
    controller._continuous_analysis = True
    controller._stable_frames = 3
    controller._last_diagnostic_signature = None
    players = roster()
    metadata = {"generation": 5, "vision_backend": "learned",
                "diagnostics": {p.player_key: {"localized": True} for p in players}}
    for captured_at in (0., .1):
        OverlayController._analysis_completed(controller, AnalysisResult("方案", status="solved"),
                                               players, {**metadata, "captured_at": captured_at})
    assert controller._team_confirmation.confirmed is not None
    controller.apply_analysis.reset_mock()
    OverlayController._analysis_failed(controller, "截图识别失败", {"generation": 5})
    assert controller._stable_frames == 0 and controller._recognition_previous is None
    assert controller._team_confirmation.confirmed is None
    assert controller._last_result is None
    controller.scan.set_annotations.assert_called_with([])
    OverlayController._analysis_completed(controller, AnalysisResult("方案", status="solved"),
                                           players, {**metadata, "captured_at": .2})
    assert controller._stable_frames == 1
    controller.apply_analysis.assert_called_once()
    assert controller.apply_analysis.call_args.args[0].reference
