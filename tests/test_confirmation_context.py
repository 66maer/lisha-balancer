"""Safety confirmations belong to the exact preview, without any GUI startup."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog

from lisha_overlay import app as app_module
from lisha_overlay.app import HotkeyFilter, OverlayController
from lisha_overlay.layout_preferences import LayoutPreferencesDialog
from lisha_overlay.team_layout import TeamLayout


class _Check:
    def __init__(self, checked=True):
        self.checked = checked

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        self.checked = bool(checked)


def _region(scan, inset):
    rect = scan.geometry()
    return {"left": rect.x() + inset, "top": rect.y() + inset,
            "width": rect.width() - 2 * inset, "height": rect.height() - 2 * inset}


def _controller():
    screen = SimpleNamespace(name=lambda: "PRIMARY", availableGeometry=lambda: QRect(0, 0, 2000, 1200))
    scan = Mock(INSET=4)
    geometry = [QRect(96, 116, 328, 248)]
    scan.geometry.side_effect = lambda: QRect(geometry[0])
    scan.setGeometry.side_effect = lambda rect: geometry.__setitem__(0, QRect(rect))
    scan.screen.return_value = screen
    controller = SimpleNamespace(
        app=SimpleNamespace(screens=lambda: [screen]), scan=scan, panel=Mock(), preview=Mock(),
        _quitting=False, _dialog=None, _generation=4, _recognition_previous=("old",),
        _analysis_attempts=3, _analysis_pending=True, _processing_busy=False,
        _players=[object()], _last_result=object(), _metadata={}, _frame=QImage(),
        running=False, locked=True, test_enabled=False, pinned=True,
        isolation_confirmed=True, manual_overrides={"g1p1": {"color": "red", "stacks": 2, "isolated": True}},
        team_layout=TeamLayout(), tank_key="g1p1", snapshot_stage="before_tank", analysis_mode="all",
        panel_opacity=.96, border_opacity=.92, panel_passthrough=False,
        hotkeys=SimpleNamespace(labels={HotkeyFilter.LOCK_ID: "Ctrl+Alt+L"}),
        region_requested=Mock(), start_requested=Mock(), stop_requested=Mock(), analysis_requested=Mock(),
        _exclude_windows=Mock(return_value=True), set_locked=Mock(), set_pinned=Mock(),
        _dock_panel=Mock(), persist=Mock(), reset_layout=Mock(),
        _cancel_tank_selection=Mock(), _refresh_tank_button=Mock(),
    )
    controller._region = _region(scan, 4)
    controller._set_running = lambda running: setattr(controller, "running", running)
    for name in ("_invalidate_analysis", "_on_capture_error", "_update_region", "_refresh_summary", "_preview_layout"):
        setattr(controller, name, MethodType(getattr(OverlayController, name), controller))
    return controller


@pytest.mark.parametrize("event", ["changed", "invalid", "worker_failure"])
def test_capture_context_loss_cannot_forward_old_confirmation_after_restart(monkeypatch, event):
    controller = _controller()
    monkeypatch.setattr(app_module, "native_client_region", _region)
    if event == "changed":
        controller.scan.setGeometry(controller.scan.geometry().translated(40, 20))
        assert controller._update_region()
    elif event == "invalid":
        original = dict(controller._region)
        monkeypatch.setattr(app_module, "native_client_region",
                            Mock(side_effect=[ValueError("Invalid selection"), original]))
        assert not controller._update_region()
    else:
        controller.running = True
        controller._on_capture_error("Capture device disconnected")

    OverlayController.toggle_capture(controller)
    assert controller.running
    controller.start_requested.emit.assert_called_once()
    OverlayController._submit_analysis(controller, QImage(), {})
    config = controller.analysis_requested.emit.call_args.args[2]
    assert config["overrides"] == {}
    assert config["isolation_confirmed"] is False


def _dialog(controller):
    dialog = Mock()
    rect = controller.scan.geometry()
    for name, value in (("x_box", rect.x()), ("y_box", rect.y()), ("width_box", rect.width()),
                        ("height_box", rect.height()), ("panel_slider", 96), ("border_slider", 92)):
        getattr(dialog, name).value.return_value = value
    dialog.screen_box.currentData.return_value = "PRIMARY"
    dialog.pass_box.isChecked.return_value = False
    dialog.remote_box.isChecked.return_value = False
    dialog.tank_box.currentData.return_value = "g1p1"
    dialog.stage_box.currentData.return_value = "before_tank"
    dialog.selected_layout = controller.team_layout
    dialog.overrides = {key: dict(value) for key, value in controller.manual_overrides.items()}
    dialog.isolation_box = _Check()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    return dialog


@pytest.mark.parametrize("change", ["same", "edited_position", "reset_during_dialog", "invalid"])
def test_settings_only_accept_confirmation_for_the_original_preview_region(monkeypatch, change):
    controller = _controller()
    controller.running = True
    dialog = _dialog(controller)
    original_overrides = dict(dialog.overrides)
    monkeypatch.setattr(app_module, "native_client_region", _region)
    monkeypatch.setattr(app_module, "exclude_from_capture", lambda window: True)
    monkeypatch.setattr(app_module, "LayoutPreferencesDialog", lambda *args, **kwargs: dialog)

    if change == "edited_position":
        dialog.x_box.value.return_value += 80
    elif change == "reset_during_dialog":
        def reset_then_accept():
            rect = controller.scan.geometry().translated(80, 0)
            controller.scan.setGeometry(rect)
            controller._region = _region(controller.scan, 4)
            dialog.x_box.value.return_value = rect.x()
            return QDialog.DialogCode.Accepted
        dialog.exec.side_effect = reset_then_accept
    elif change == "invalid":
        monkeypatch.setattr(app_module, "native_client_region", Mock(side_effect=ValueError("Invalid selection")))

    OverlayController.open_preferences(controller)
    OverlayController._submit_analysis(controller, QImage(), {})
    config = controller.analysis_requested.emit.call_args.args[2]
    assert config["isolation_confirmed"] is (change == "same")
    assert config["overrides"] == (original_overrides if change == "same" else {})


@pytest.mark.parametrize("case", ["initial_rounding", "unchanged", "right_margin", "marker_region"])
def test_layout_rebuild_preserves_initial_context_but_invalidates_changed_grid(case):
    right = .01 if case == "right_margin" else .08000000000000002 if case == "initial_rounding" else .08
    layout = TeamLayout.uniform(right=right)
    options = layout.grid_options
    form = Mock()
    form._selected_layout = layout
    form._layout_initialized = case != "initial_rounding"
    form.formation_box.currentText.return_value = "3+2"
    form.columns_box.value.return_value = 5
    form.grid_spins = {key: Mock() for key in options if key != "formation"}
    for key, spin in form.grid_spins.items():
        spin.value.return_value = round(options[key] * 100, 2)
    form.grid_spins["right"].value.return_value = 8
    form._rects = {key: list(getattr(layout, key)) for key in ("name_rect", "buff_rect", "marker_rect")}
    if case == "marker_region":
        form._rects["marker_rect"] = [.60, .43, .38, .53]
    form.overrides = {"g1p1": {"isolated": True}}
    form.isolation_box = _Check()

    LayoutPreferencesDialog._rebuild(form)

    preserved = case in ("initial_rounding", "unchanged")
    assert bool(form.overrides) is preserved
    assert form.isolation_box.isChecked() is preserved
    assert form._layout_initialized is True
    assert form._selected_layout.grid_options["right"] == pytest.approx(.08)
