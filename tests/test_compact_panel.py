"""Compact toolbar controls remain available without growing into a dashboard."""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QAbstractButton, QLabel, QMessageBox, QWidget

from lisha_overlay import __version__
from lisha_overlay.panel import ControlPanel


def test_full_title_and_about_button_fit_the_compact_header(qt_app):
    panel = ControlPanel()
    try:
        panel.show()
        qt_app.processEvents()
        header = panel.findChild(QWidget, "dragHandle")
        title = panel.findChild(QLabel, "panelTitle")

        assert panel.size() == QSize(232, 108)
        assert panel.windowTitle() == title.text() == "戾煞配平助手"
        assert title.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert title.width() >= title.fontMetrics().horizontalAdvance(title.text())
        assert panel.about_button.isVisible()
        assert header.rect().contains(panel.about_button.geometry())
        assert title.geometry().right() < panel.about_button.geometry().left()
        assert panel.about_button.geometry().right() < panel.pin_button.geometry().left()
    finally:
        panel.hide()
        panel.deleteLater()


def test_about_button_opens_version_and_author_information(qt_app):
    panel = ControlPanel()
    texts = []

    def read_and_close_about():
        for widget in qt_app.topLevelWidgets():
            if isinstance(widget, QMessageBox):
                texts.append((widget.windowTitle(), widget.text()))
                widget.accept()

    try:
        panel.show()
        QTimer.singleShot(0, read_and_close_about)
        panel.about_button.click()
        assert texts == [("版本信息", f"戾煞配平助手\n版本 {__version__}\n\nby 华乾·丐箩箩")]
    finally:
        panel.hide()
        panel.deleteLater()


def test_trial_toolbar_hides_settings_test_and_attachment_controls(qt_app):
    panel = ControlPanel()
    calls = []
    panel.settings_requested.connect(lambda: calls.append("settings"))
    try:
        panel.show()
        qt_app.processEvents()

        assert panel.size() == QSize(232, 108)
        assert panel.sizeHint() == QSize(232, 108)
        names = ("aboutButton", "pinButton", "closeButton")
        controls = [panel.findChild(QAbstractButton, name) for name in names]
        assert all(button is not None and button.isVisible() for button in controls)
        assert [button.x() for button in controls] == sorted(button.x() for button in controls)
        assert len({button.y() for button in controls}) == 1
        assert all(button.size() == QSize(20, 20) for button in controls)
        assert all(not button.icon().isNull() for button in controls)
        assert all(not panel.findChild(QAbstractButton,name).isVisible()
                   for name in ('settingsButton','testButton','attachButton'))

        header = panel.findChild(QWidget, "dragHandle")
        modes = panel.findChild(QWidget, "modeSegments")
        summary = panel.findChild(QLabel, "summaryLabel")
        assert header.geometry().bottom() < modes.geometry().top()
        assert modes.geometry().bottom() < summary.geometry().top()
        assert panel.rect().contains(summary.geometry())
        assert len([button for button in panel.findChildren(QAbstractButton) if button.isVisible()]) == 7
        assert not panel.tank_button.isVisible()
        instruction=panel.findChild(QLabel,'instructionLabel')
        assert summary.geometry().bottom()<instruction.geometry().top()
        assert panel.rect().contains(instruction.geometry())

        assert calls == []
    finally:
        panel.hide()
        panel.deleteLater()


def test_programmatic_toolbar_state_does_not_emit_user_actions(qt_app):
    panel = ControlPanel()
    calls = []
    panel.mode_changed.connect(lambda mode: calls.append(("mode", mode)))
    panel.test_toggled.connect(lambda enabled: calls.append(("test", enabled)))
    panel.pin_toggled.connect(lambda pinned: calls.append(("pin", pinned)))
    try:
        assert panel.mode_buttons["all"].isChecked()
        assert panel.pin_button.isChecked()
        assert not panel.test_button.isChecked()

        panel.set_mode("circle")
        panel.set_test_state(True)
        panel.set_pinned(False)
        assert calls == []
        assert panel.mode_buttons["circle"].isChecked()
        assert panel.test_button.isChecked()
        assert not panel.pin_button.isChecked()

        panel.mode_buttons["all"].click()
        panel.test_button.click()
        panel.pin_button.click()
        assert calls == [("mode", "all"), ("test", False), ("pin", True)]
    finally:
        panel.deleteLater()


def test_each_click_executes_even_when_same_mode_is_already_selected(qt_app):
    panel = ControlPanel()
    calls = []
    panel.mode_changed.connect(calls.append)
    for mode in ("all", "all", "circle", "circle"):
        panel.mode_buttons[mode].click()
    assert calls == ["all", "all", "circle", "circle"]
    panel.deleteLater()


def test_start_stop_action_is_independent_of_mode_selection(qt_app):
    panel = ControlPanel()
    actions, modes = [], []
    panel.analysis_toggled.connect(lambda: actions.append("toggle"))
    assert not panel.start_button.isEnabled()
    panel.start_button.click()
    assert not actions
    panel.set_grid_ready(True)
    panel.mode_changed.connect(modes.append)
    panel.mode_buttons["circle"].click()
    assert modes == ["circle"] and not actions
    panel.start_button.click()
    panel.set_analysis_active(True)
    assert panel.start_button.toolTip() == "停止识别"
    panel.start_button.click()
    panel.set_analysis_active(False)
    assert panel.start_button.toolTip() == "开始识别"
    assert actions == ["toggle", "toggle"] and modes == ["circle"]
    assert panel.mode_buttons["circle"].isChecked()
    panel.deleteLater()


def test_grid_action_fits_without_tank_control_and_busy_state_blocks_it(qt_app):
    panel = ControlPanel()
    calls = []
    panel.grid_requested.connect(lambda: calls.append("grid"))
    panel.tank_requested.connect(lambda: calls.append("tank"))
    try:
        panel.show()
        qt_app.processEvents()
        assert panel.size() == QSize(232, 108)
        assert panel.grid_button.toolTip() == "自动定位团队格子"
        assert not panel.tank_button.isVisible()
        segments = panel.findChild(QWidget, "modeSegments")
        assert not panel.start_button.isEnabled()
        panel.set_grid_ready(True)
        controls = [*panel.mode_buttons.values(), panel.start_button, panel.grid_button]
        assert all(segments.rect().contains(button.geometry()) for button in controls)
        assert all(button.width() >= 40 for button in panel.mode_buttons.values())
        panel.grid_button.click()
        assert calls == ["grid"]

        panel.set_grid_busy(True)
        assert not any(button.isEnabled() for button in controls)
        panel.grid_button.click()
        assert calls == ["grid"]
        panel.set_grid_busy(False)
        assert all(button.isEnabled() for button in controls)

        panel.set_tank_selecting(True)
        assert panel.tank_button.isEnabled()
        assert not panel.grid_button.isEnabled()
        assert not any(button.isEnabled() for button in panel.mode_buttons.values())
        panel.set_grid_busy(True)
        panel.set_grid_busy(False)
        assert not panel.grid_button.isEnabled()
        panel.set_tank_selecting(False)
        assert all(button.isEnabled() for button in controls)
    finally:
        panel.hide()
        panel.deleteLater()


def test_count_colours_signed_difference_and_no_solution_survive_detail_update(qt_app):
    from lisha_overlay.analysis import AnalysisResult
    panel=ControlPanel()
    try:
        panel.set_result(AnalysisResult('红 6  黑 8  差 -2',status='no_solution',
            current_red=6,current_black=8,instruction='当前站位无解'))
        before=panel._summary_label.text()
        panel.set_status('当前站位没有可用方案。\n详细信息保留在提示中。')
        assert panel._summary_label.text()==before
        assert '<b style="color:#b8233f">红 6</b>' in before
        assert '<b style="color:#12171d">黑 8</b>' in before
        assert '<b style="color:#12171d">差 -2</b>' in before
        assert panel._instruction_label.text()=='当前站位无解'
        panel.set_result(AnalysisResult('红 8  黑 6  差 +2',status='solved',
            current_red=8,current_black=6,instruction='3红一排圈'))
        assert '<b style="color:#b8233f">差 +2</b>' in panel._summary_label.text()
    finally:panel.deleteLater()


def test_live_solution_keeps_buff_types_and_overlap_in_instruction_tooltip(qt_app):
    from lisha_overlay.analysis import evaluate_current
    from test_strategy_policy import roster
    panel=ControlPanel()
    try:
        panel.show();qt_app.processEvents()
        result=evaluate_current(roster([1]*15+[-1]*10))
        panel.set_result(result);qt_app.processEvents()
        assert panel.width()==232 and panel.height()<=126
        assert '红一' in panel._instruction_label.text()
        assert result.instruction in panel._instruction_label.toolTip()
        panel.set_summary('画面待确认')
        assert panel._instruction_label.text()==''
        assert panel.height()==108
    finally:panel.deleteLater()


def test_balanced_word_is_green_and_clears_when_a_new_correction_is_needed(qt_app):
    from lisha_overlay.analysis import evaluate_current
    from test_strategy_policy import roster
    panel=ControlPanel()
    try:
        panel.set_result(evaluate_current(roster([1]*6+[-1]*8)))
        assert '<b style="color:#087f45">平衡</b>' in panel._summary_label.text()
        assert panel._instruction_label.text()=='黑一/黑二引导黑圈'
        panel.set_result(evaluate_current(roster([1]*15+[-1]*10),'circle'))
        assert '平衡' not in panel._summary_label.text()
        assert '红一排圈' in panel._instruction_label.text()
    finally:panel.deleteLater()


def test_reference_hint_is_visible_with_rich_counts_and_clears_with_status(qt_app):
    from lisha_overlay.analysis import AnalysisResult
    panel=ControlPanel()
    try:
        panel.set_result(AnalysisResult('候选结果',status='solved',current_red=12,current_black=10,
            balanced=True,instruction='黑一/黑二引导黑圈',reference=True))
        assert '参考' in panel._summary_label.text() and '平衡' in panel._summary_label.text()
        assert '参考' in panel._summary_label.toolTip()
        assert panel.width()==232 and panel.height()<=126
        panel.set_summary('格子待定位')
        assert '参考' not in panel._summary_label.text()
    finally:panel.deleteLater()
