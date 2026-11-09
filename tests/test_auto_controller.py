"""Controller regressions using synthetic geometry and local state only."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QImage

from lisha_overlay import app as app_module
from lisha_overlay.analysis import AnalysisResult
from lisha_overlay.app import OverlayController
from lisha_overlay.layout_preferences import LayoutPreferencesDialog
from lisha_overlay.platform_win import _map_rect_to_monitor
from lisha_overlay.team_layout import Annotation, GroupGridLayout, TeamLayout


class _Picker:
    def __init__(self):
        self.is_selecting = False
        self.visible = False
        self.starts = 0
        self.on_cancel = lambda: None

    def isVisible(self):
        return self.visible

    def set_grid(self, layout, geometry):
        self.layout, self.geometry = layout, QRect(geometry)

    def set_current_tank(self, key):
        self.current = key

    def start_selection(self):
        self.starts += 1
        self.is_selecting = self.visible = True
        return True

    def cancel_selection(self):
        active = self.is_selecting
        self.is_selecting = self.visible = False
        if active:
            self.on_cancel()


def _detected_layout():
    # Deliberately unequal boundaries, as an image detector may observe.
    rects = ((.10, .10, .09, .30), (.205, .105, .095, .298),
             (.315, .10, .09, .302), (.102, .45, .092, .30),
             (.21, .452, .093, .297))
    return TeamLayout(groups=tuple(GroupGridLayout(i + 1, rect) for i, rect in enumerate(rects)))


@pytest.fixture
def controller(monkeypatch):
    display = SimpleNamespace(rect=QRect(-800, 0, 800, 600), dpr=1.25)
    display.name = lambda: "DISPLAY-A"
    display.geometry = lambda: QRect(display.rect)
    display.availableGeometry = display.geometry
    display.devicePixelRatio = lambda: display.dpr
    physical = {"left": -1200, "top": 0, "width": 1000, "height": 750}
    scan = Mock(INSET=4)
    rectangle = [QRect(-780, 20, 250, 350)]
    scan.geometry.side_effect = lambda: QRect(rectangle[0])
    scan.setGeometry.side_effect = lambda rect: rectangle.__setitem__(0, QRect(rect))
    scan.screen.return_value = display
    scan.markers.content_rect.side_effect = lambda: QRectF(4, 4, rectangle[0].width() - 8, rectangle[0].height() - 8)
    panel = Mock()
    panel.screen.return_value = display
    preview = Mock()
    preview.isVisible.return_value = False
    picker = _Picker()
    instance = SimpleNamespace(
        app=SimpleNamespace(screens=lambda: [display]), scan=scan, panel=panel,
        preview=preview, tank_picker=picker, _quitting=False, _dialog=None,
        _grid_generation=17, _grid_busy=False, _auto_applying=False, _pick_after_grid=False,
        grid_ready=True, running=True, locked=True, test_enabled=False,
        team_layout=TeamLayout(), tank_key="g1p1", snapshot_stage="before_tank", analysis_mode="all",
        _generation=5, _recognition_previous=("old",), _analysis_attempts=3,
        _analysis_pending=True, _processing_busy=False, _players=[object()],
        _last_result=AnalysisResult("旧方案", [Annotation("g1p1", "circle")], "solved"),
        manual_overrides={"g1p1": {"isolated": True}}, isolation_confirmed=True,
        _frame=QImage(), _metadata={}, _record_grid=Mock(), _grid_feedback_timer=Mock(),
        _save_timer=Mock(), grid_requested=Mock(), region_requested=Mock(),
        _exclude_windows=Mock(return_value=True), _dock_panel=Mock(), _avoid_panel_overlap=Mock(),
        toggle_capture=Mock(), set_test_enabled=Mock(), _tank_ui=False, _grid_ui=False,
    )
    instance.saved = []
    instance.persist = Mock(side_effect=lambda: instance.saved.append({
        "tank_key": instance.tank_key, "grid_ready": instance.grid_ready,
        "team_layout": instance.team_layout.to_dict(),
    }))
    panel.set_tank_selecting.side_effect = lambda active: setattr(instance, "_tank_ui", active)
    panel.set_grid_busy.side_effect = lambda active: setattr(instance, "_grid_ui", active)

    def native_region(widget, inset):
        return _map_rect_to_monitor(widget.geometry().adjusted(inset, inset, -inset, -inset),
                                    display.geometry(), physical, display.dpr)

    monkeypatch.setattr(app_module, "native_client_region", native_region)
    instance._region = native_region(scan, scan.INSET)
    for name in ("_invalidate_analysis", "_refresh_summary", "_refresh_tank_button",
                 "_cancel_tank_selection", "_tank_selection_cancelled", "_tank_selected",
                 "select_tank", "locate_grid", "_update_region", "_grid_failed"):
        setattr(instance, name, MethodType(getattr(OverlayController, name), instance))
    picker.on_cancel = instance._tank_selection_cancelled
    instance.display, instance.physical = display, physical
    return instance


def _result(controller):
    layout = _detected_layout()
    source=dict(controller._region)
    frame = QImage(source['width'],source['height'],QImage.Format.Format_RGB32)
    frame.fill(QColor("#263746"))
    cells = layout.cells(QRectF(frame.rect()))
    bounds = QRectF(cells[0].rect)
    for cell in cells[1:]:
        bounds = bounds.united(cell.rect)
    detection = SimpleNamespace(layout=layout, bounds=bounds, confidence=.96)
    metadata = {
        "generation": controller._grid_generation, "capture_region":source,
        "scan_geometry":list(controller.scan.geometry().getRect()),
        "search_scope":"selection",
        "screen_name": controller.display.name(), "screen_geometry": [-800, 0, 800, 600],
        "dpr": 1.25, "captured_at": 1234.,
    }
    return detection, frame, metadata


def test_selected_tank_replaces_the_key_clears_old_plan_and_persists(controller):
    controller.select_tank()
    assert controller.tank_picker.is_selecting and controller._tank_ui
    controller._tank_selected("g4p2")
    assert controller.tank_key == "g4p2"
    assert not controller.tank_picker.isVisible() and not controller._tank_ui
    assert controller._last_result is None and controller._players == []
    assert not controller.manual_overrides and not controller.isolation_confirmed
    controller.scan.set_annotations.assert_called_with([])
    assert controller.saved[-1]["tank_key"] == "g4p2"


def test_cancel_selection_keeps_tank_without_leaving_a_selection_surface(controller):
    controller.select_tank()
    controller.tank_picker.cancel_selection()
    assert controller.tank_key == "g1p1"
    assert not controller.tank_picker.is_selecting
    assert not controller.tank_picker.isVisible() and not controller._tank_ui
    controller.persist.assert_not_called()


def test_located_grid_allows_analysis_without_forcing_tank_selection(controller):
    controller.grid_ready = True
    controller.tank_key = None
    controller._last_result = None
    controller._refresh_summary()
    controller.panel.set_summary.assert_called_with("待分析")


def test_analyze_without_calibration_is_blocked_without_automatic_start(controller):
    controller.grid_ready = False
    controller.tank_key = None
    controller.locate_grid = Mock()
    OverlayController.analyze_mode(controller, "fan")
    controller.locate_grid.assert_not_called()
    controller.panel.set_summary.assert_called_with('请先校准网格')
    assert controller.analysis_mode == "all"


def test_grid_completion_continues_requested_analysis_without_selecting_tank(controller):
    detection, frame, metadata = _result(controller)
    controller._grid_busy = True
    controller._analyze_after_grid = "circle"
    controller.tank_key = None
    controller.analyze_mode = Mock()
    OverlayController._grid_completed(controller, detection, frame, metadata)
    controller.analyze_mode.assert_called_once_with("circle")
    assert controller._analyze_after_grid is None
    assert controller.tank_picker.starts == 0


def test_buff_recognition_status_survives_toolbar_state_refresh(controller):
    controller.grid_ready = True
    controller._last_result = AnalysisResult("Buff待识别 · 10人", status="needs_recognition")
    controller._refresh_summary()
    controller.panel.set_result.assert_called_with(controller._last_result)


def test_first_tank_selection_locates_grid_before_opening_picker(controller):
    controller.grid_ready = False
    controller.tank_key = None
    controller.locate_grid = Mock()
    controller.select_tank()
    controller.locate_grid.assert_called_once_with(pick_tank=True)
    assert controller.tank_picker.starts == 0


def test_location_request_is_single_flight_and_carries_monitor_context(controller):
    controller.locate_grid(pick_tank=True)
    assert controller._grid_busy and controller._grid_ui
    assert not controller.grid_ready and controller.tank_key is None
    config = controller.grid_requested.emit.call_args.args[0]
    assert config["capture_region"] == controller._region
    assert config['search_scope']=='selection'
    assert config["screen_geometry"] == [-800, 0, 800, 600]
    assert config["dpr"] == 1.25 and config["screen_name"] == "DISPLAY-A"
    assert controller._pick_after_grid
    controller.locate_grid()
    controller.grid_requested.emit.assert_called_once()


def test_detached_toolbar_on_another_monitor_does_not_change_game_capture(controller):
    toolbar_display=SimpleNamespace(name=lambda:'DISPLAY-B',geometry=lambda:QRect(0,0,1920,1080),
                                    devicePixelRatio=lambda:1.)
    controller.panel.screen.return_value=toolbar_display
    controller.attached=False
    controller.locate_grid()
    config=controller.grid_requested.emit.call_args.args[0]
    assert config['screen_name']=='DISPLAY-A'
    assert config['screen_geometry']==[-800,0,800,600]
    assert config['capture_region']==controller._region
    assert config['capture_region']!=controller.physical


def test_initial_auto_location_continues_into_tank_picker(controller):
    controller.grid_ready = False
    controller.tank_key = None
    controller.select_tank()
    assert controller._grid_busy and controller._pick_after_grid
    detection, frame, metadata = _result(controller)
    OverlayController._grid_completed(controller, detection, frame, metadata)
    assert controller.grid_ready and not controller._grid_busy
    assert not controller._pick_after_grid
    assert controller.tank_picker.is_selecting and controller._tank_ui
    assert controller.tank_picker.layout is controller.team_layout
    assert controller.tank_picker.geometry == controller.scan.geometry()


def test_stale_grid_result_and_failure_cannot_change_the_current_request(controller):
    controller._grid_busy = True
    old_layout = controller.team_layout
    old_rect = controller.scan.geometry()
    metadata = {"generation": controller._grid_generation - 1}
    OverlayController._grid_completed(controller, object(), QImage(), metadata)
    controller._grid_failed("old failure", metadata)
    assert controller._grid_busy
    assert controller.team_layout is old_layout and controller.scan.geometry() == old_rect
    controller.scan.setGeometry.assert_not_called()
    controller.persist.assert_not_called()
    controller.panel.set_grid_busy.assert_not_called()


@pytest.mark.parametrize("change", ["geometry", "dpr", "removed"])
def test_changed_monitor_rejects_detection_and_restores_original_layout(controller, change):
    detection, frame, metadata = _result(controller)
    controller._grid_busy = True
    old_layout, old_geometry = controller.team_layout, controller.scan.geometry()
    if change == "geometry":
        controller.display.rect.translate(10, 0)
    elif change == "dpr":
        controller.display.dpr = 1.5
    else:
        controller.app.screens = lambda: []
    OverlayController._grid_completed(controller, detection, frame, metadata)
    assert not controller.grid_ready and not controller._grid_busy and not controller._grid_ui
    assert controller.team_layout is old_layout
    assert controller.scan.geometry() == old_geometry
    assert "显示器已变化" in controller.panel.set_status.call_args.args[0]
    assert controller._record_grid.call_args.args[0] == "not_found"


def test_calibration_keeps_user_frame_and_uses_the_identical_recognition_roi(controller):
    detection, frame, metadata = _result(controller)
    controller._grid_busy = True

    original_geometry=controller.scan.geometry()
    original_region=dict(controller._region)
    OverlayController._grid_completed(controller, detection, frame, metadata)
    assert controller.grid_ready and not controller._grid_busy and not controller._auto_applying
    assert controller.scan.geometry()==original_geometry
    assert controller._region==original_region==metadata['capture_region']
    controller.scan.setGeometry.assert_not_called()
    controller._dock_panel.assert_not_called()
    controller._avoid_panel_overlap.assert_not_called()
    controller.toggle_capture.assert_not_called()
    assert controller._frame.width() == controller._region["width"]
    assert controller._frame.height() == controller._region["height"]
    assert controller._metadata["region"] == controller._region
    assert controller._frame.pixelColor(0, 0) == QColor("#263746")
    source_cells = detection.layout.cells(QRectF(frame.rect()))
    target_cells = controller.team_layout.cells(QRectF(controller._frame.rect()))
    for source, target in zip(source_cells, target_cells):
        assert source.key == target.key
        for field in ("rect", "name_rect", "buff_rect", "marker_rect"):
            before, after = getattr(source, field), getattr(target, field)
            assert before.x() == pytest.approx(after.x())
            assert before.y() == pytest.approx(after.y())
            assert before.width() == pytest.approx(after.width())
            assert before.height() == pytest.approx(after.height())
    assert controller.saved[-1]["grid_ready"] is True


def test_moved_selection_rejects_old_result_without_restoring_or_resizing_frame(controller):
    detection,frame,metadata=_result(controller)
    controller._grid_busy=True
    moved=controller.scan.geometry().translated(20,10)
    controller.scan.setGeometry(moved);controller.scan.setGeometry.reset_mock()
    OverlayController._grid_completed(controller,detection,frame,metadata)
    assert not controller.grid_ready
    assert controller.scan.geometry()==moved
    controller.scan.setGeometry.assert_not_called()
    assert '框选范围已变化' in controller._record_grid.call_args.kwargs['error']


def _settings_dialog(qt_app):
    layout = _detected_layout()
    display = qt_app.primaryScreen()
    scan = SimpleNamespace(screen=lambda: display, x=lambda: 10, y=lambda: 20,
                           width=lambda: 400, height=lambda: 500)
    dialog = LayoutPreferencesDialog([display], scan, .96, .92, False,
                                     team_layout=layout, overrides={"g2p3": {"isolated": True}},
                                     isolation_confirmed=True)
    return layout, dialog


def test_opening_settings_keeps_detected_individual_column_boundaries(qt_app):
    layout, dialog = _settings_dialog(qt_app)
    try:
        assert dialog.selected_layout.groups == layout.groups
        assert dialog.preview.team_layout.groups == layout.groups
        assert dialog.overrides == {"g2p3": {"isolated": True}}
        assert dialog.isolation_box.isChecked()
        assert len({group.rect[2] for group in dialog.selected_layout.groups}) > 1
    finally:
        dialog.deleteLater()


def test_editing_only_buff_roi_preserves_detected_columns_and_clears_confirmation(qt_app):
    layout, dialog = _settings_dialog(qt_app)
    try:
        dialog.rect_spins[0].setValue(2.5)
        assert dialog.selected_layout.groups == layout.groups
        assert dialog.preview.team_layout.groups == layout.groups
        assert dialog.selected_layout.buff_rect[0] == .025
        assert not dialog.overrides
        assert not dialog.isolation_box.isChecked()
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("saved", [[-804, -4, 328, 500], [-780, 120, 300, 484]])
def test_detected_grid_at_monitor_edge_keeps_exact_alignment_after_restart(controller, saved):
    display = controller.display
    display.availableGeometry = lambda: QRect(-800, 0, 800, 560)
    controller.app.primaryScreen = lambda: display
    controller.app.screenAt = lambda point: display if display.geometry().contains(point) else None
    controller.settings = {"scan_geometry": saved}
    controller.attached = True
    controller.grid_ready = True
    OverlayController._place_initial(controller)
    assert controller.scan.geometry() == QRect(*saved)
    assert display.geometry().contains(controller.scan.geometry().adjusted(4, 4, -4, -4))
