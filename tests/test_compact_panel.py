"""Compact toolbar controls remain available without growing into a dashboard."""

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
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

        assert panel.size() == QSize(360, 178)
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

        assert panel.size() == QSize(360, 178)
        assert panel.sizeHint() == QSize(360, 178)
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
        summary_area = panel.findChild(QWidget, 'summaryArea')
        buffs = panel.findChild(QWidget, 'buffCountsRow')
        assert header.geometry().bottom() < modes.geometry().top()
        assert modes.geometry().bottom() < summary_area.geometry().top()
        assert panel.rect().contains(summary_area.geometry())
        assert summary.mapTo(panel, QPoint()).y() + summary.height() <= buffs.mapTo(panel, QPoint()).y()
        assert len([button for button in panel.findChildren(QAbstractButton) if button.isVisible()]) == 8
        assert not panel.tank_button.isVisible()
        instruction=panel.findChild(QLabel,'instructionLabel')
        assert summary_area.geometry().bottom()<instruction.mapTo(panel, QPoint()).y()
        assert panel.rect().contains(panel._command_area.geometry())

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
        assert panel.size() == QSize(360, 178)
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
        assert '差 -2' in before and '✓' in before
        assert '当前站位无解' in panel._instruction_label.text()
        panel.set_result(AnalysisResult('红 8  黑 6  差 +2',status='solved',
            current_red=8,current_black=6,instruction='3红一排圈'))
        assert '差 +2' in panel._summary_label.text() and '✓' in panel._summary_label.text()
    finally:panel.deleteLater()


def test_live_solution_keeps_buff_types_and_overlap_in_instruction_tooltip(qt_app):
    from lisha_overlay.live_strategy import evaluate_current
    from test_strategy_policy import roster
    panel=ControlPanel()
    try:
        panel.show();qt_app.processEvents()
        result=evaluate_current(roster([1]*15+[-1]*10))
        panel.set_result(result);qt_app.processEvents()
        assert panel.size() == QSize(360, 178)
        assert '<img ' in panel._instruction_labels[1].text()
        assert result.instruction in panel._instruction_label.toolTip()
        panel.set_summary('画面待确认')
        assert panel._instruction_label.text()==''
        assert panel.size() == QSize(360, 178)
    finally:panel.deleteLater()


def test_safe_check_clears_when_a_new_correction_is_needed(qt_app):
    from lisha_overlay.live_strategy import evaluate_current
    from test_strategy_policy import roster
    panel=ControlPanel()
    try:
        panel.set_result(evaluate_current(roster([1]*6+[-1]*8)))
        assert '<b style="color:#12171d">差 -2</b>' in panel._summary_label.text()
        assert '✓' in panel._summary_label.text()
        assert '引导圈' in panel._instruction_labels[1].text()
        panel.set_result(evaluate_current(roster([1]*15+[-1]*10),'circle'))
        assert '✓' not in panel._summary_label.text()
        assert '引导圈' in panel._instruction_labels[1].text()
    finally:panel.deleteLater()


def test_reference_hint_is_visible_with_rich_counts_and_clears_with_status(qt_app):
    from lisha_overlay.analysis import AnalysisResult
    panel=ControlPanel()
    try:
        panel.set_result(AnalysisResult('候选结果',status='solved',current_red=12,current_black=10,
            balanced=True,instruction='黑一/黑二引导黑圈',reference=True))
        panel.show()
        qt_app.processEvents()
        assert panel._reference_label.isVisible() and '✓' in panel._summary_label.text()
        assert panel._summary_label.geometry().right() < panel._reference_label.geometry().left()
        assert '参考' in panel._summary_label.toolTip()
        assert panel.size() == QSize(360, 178)
        panel.set_summary('格子待定位')
        assert panel._reference_label.isHidden()
    finally:panel.deleteLater()


def test_buff_counts_use_six_fixed_badges_and_clear_with_unavailable_state(qt_app):
    from lisha_overlay.analysis import AnalysisResult
    from lisha_overlay.live_strategy import evaluate_current
    from test_strategy_policy import roster
    panel = ControlPanel()
    try:
        panel.show()
        panel.set_result(evaluate_current(roster([1]*4+[2]*3+[3]*2+[-1]*5+[-2]*4+[-3]*3+[0]*4)))
        qt_app.processEvents()
        assert list(panel._buff_count_labels) == ['red1', 'red2', 'red3', 'black1', 'black2', 'black3']
        assert [label.text() for label in panel._buff_count_labels.values()] == ['4', '3', '2', '5', '4', '3']
        panel.set_result(AnalysisResult('三层及以上', current_red=6, current_black=4,
                                       buff_counts={'red3': 2, 'red4': 3, 'red5': 1, 'black3': 2, 'black4': 2}))
        assert panel._buff_count_labels['red3'].text() == '6'
        assert panel._buff_count_labels['black3'].text() == '4'
        row = panel._buff_counts_row
        for label in row.findChildren(QLabel):
            assert row.rect().contains(label.geometry())
        assert panel._summary_area.geometry().bottom() < panel._command_area.geometry().top()
        panel.set_summary('画面待确认')
        assert all(label.text() == '—' for label in panel._buff_count_labels.values())
        panel.set_result(evaluate_current(roster([0]*25)))
        assert all(label.text() == '0' for label in panel._buff_count_labels.values())
    finally:
        panel.hide()
        panel.deleteLater()


def test_difficulty_toggle_reclassifies_current_and_final_difference_without_resizing(qt_app):
    from lisha_overlay.analysis import AnalysisResult
    panel = ControlPanel()
    changes = []
    panel.difficulty_changed.connect(changes.append)
    try:
        panel.set_result(AnalysisResult('当前', current_red=12, current_black=10, final_red=10, final_black=12))
        assert '✓' in panel._summary_label.text() and '✓' in panel._result_label.text()
        panel.difficulty_button.click()
        assert changes == ['heroic']
        assert '<b style="color:#c37c00">危</b>' in panel._summary_label.text()
        assert '<b style="color:#c37c00">危</b>' in panel._result_label.text()
        assert '<b style="color:#b8233f">差 +2</b>' in panel._summary_label.text()
        assert '<b style="color:#12171d">差 -2</b>' in panel._result_label.text()
        assert '✓' not in panel._summary_label.text()
        panel.set_difficulty('normal')
        assert changes == ['heroic']
        assert '✓' in panel._summary_label.text()
        assert panel.size() == QSize(360, 178)
    finally:
        panel.deleteLater()


def test_three_command_rows_and_final_counts_keep_the_panel_size_fixed(qt_app):
    from lisha_overlay.analysis import AnalysisResult
    panel = ControlPanel()
    instruction = '\n'.join([
        '{红1} × 3、{黑2} × 2 吃扇',
        '{黑1} × 3 引导圈',
        '{红3} × 5 蹭单圈，{红2} × 2 蹭双圈',
    ])
    try:
        panel.show()
        panel.set_result(AnalysisResult('候选结果', status='solved', current_red=10, current_black=15,
                                        instruction=instruction, reference=True, final_red=10, final_black=11))
        qt_app.processEvents()
        rich = '\n'.join(label.text() for label in panel._instruction_labels)
        assert rich.count('<img ') == 5
        assert '{红1}' not in rich and '{黑2}' not in rich
        assert '× 3' in rich and '× 2' in rich
        assert len(panel._instruction_labels) == 3
        assert all(label.height() == 22 for label in panel._instruction_labels)
        assert panel.size() == QSize(360, 178)
        assert panel._result_label.parentWidget() is panel._summary_area
        assert panel._summary_area.rect().contains(panel._result_label.geometry())
        assert panel._result_label.geometry().left() > panel._buff_counts_row.geometry().right()
        assert '红 10' in panel._result_label.text() and '黑 11' in panel._result_label.text()
        assert '差 -1' in panel._result_label.text() and '✓' in panel._result_label.text()
        assert instruction in panel._instruction_label.toolTip()
        panel.set_summary('画面待确认')
        assert panel.size() == QSize(360, 178)
        assert '红 10' not in panel._result_label.text()
    finally:
        panel.hide()
        panel.deleteLater()


def test_signed_difference_colours_survive_every_severity_in_current_and_final_counts(qt_app):
    from lisha_overlay.analysis import AnalysisResult
    panel = ControlPanel()
    try:
        for difficulty in ('normal', 'heroic'):
            panel.set_difficulty(difficulty)
            for delta in (-5, -3, -2, -1, 0, 1, 2, 3, 5):
                panel.set_result(AnalysisResult('当前', current_red=10+delta, current_black=10,
                                                final_red=10+delta, final_black=10))
                color = '#b8233f' if delta > 0 else '#12171d'
                signed = f'{delta:+d}' if delta else '0'
                expected = f'<b style="color:{color}">差 {signed}</b>'
                assert expected in panel._summary_label.text()
                assert expected in panel._result_label.text()
    finally:
        panel.deleteLater()
