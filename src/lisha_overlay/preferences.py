"""Small appearance and placement dialog; scan and panel remain separate windows."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout,
)


class PreferencesDialog(QDialog):
    reset_requested = Signal()

    def __init__(self, screens, scan, panel_opacity, border_opacity,
                 panel_passthrough, parent=None):
        super().__init__(parent)
        self.setWindowTitle("戾煞配平 · 外观与位置")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumWidth(410)
        self.setStyleSheet("""
            QDialog { background: #131e29; color: #e7f0f5; }
            QLabel, QCheckBox { color: #d9e5ee; font-size: 12px; }
            QSpinBox, QDoubleSpinBox, QComboBox { background: #1f2e3d; color: #e7f0f5;
                border: 1px solid #354859; border-radius: 5px; padding: 6px; }
            QPushButton { background: #273c4a; color: #e7f0f5; padding: 7px 14px;
                border: 1px solid #3c5665; border-radius: 5px; }
            QPushButton:hover { background: #34505d; }
            QSlider::groove:horizontal { height: 4px; background: #2b3e4e; }
            QSlider::sub-page:horizontal { background: #63dfcb; }
            QSlider::handle:horizontal { background: #b9f5eb; width: 14px;
                margin: -5px 0; border-radius: 7px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(16)
        title = QLabel("外观与位置")
        title.setStyleSheet("font-size: 18px; font-weight: 600; color: #f3f8fc;")
        layout.addWidget(title)
        form = QFormLayout()
        form.setSpacing(12)
        self.screen_box = QComboBox()
        self.screen_box.setObjectName("screenCombo")
        self._screens = list(screens)
        for screen in self._screens:
            geo = screen.geometry()
            self.screen_box.addItem(
                f"{screen.name()} · {round(geo.width() * screen.devicePixelRatio())} × "
                f"{round(geo.height() * screen.devicePixelRatio())}", screen.name())
        self.screen_box.setCurrentIndex(max(0, self.screen_box.findData(scan.screen().name())))
        self.x_box = QSpinBox()
        self.y_box = QSpinBox()
        self.x_box.setObjectName("scanXSpin")
        self.y_box.setObjectName("scanYSpin")
        for box, value, axis in ((self.x_box, scan.x(), "X"), (self.y_box, scan.y(), "Y")):
            box.setRange(-100000, 100000)
            box.setSingleStep(1)
            box.setValue(value)
            box.setSuffix(" px")
            box.setToolTip(f"扫描框 {axis} 坐标（界面像素，支持负坐标）")
        position = QHBoxLayout()
        position.addWidget(self.x_box)
        position.addWidget(QLabel("/"))
        position.addWidget(self.y_box)
        self.screen_box.currentIndexChanged.connect(self._screen_changed)
        self.width_box = QSpinBox()
        self.height_box = QSpinBox()
        self.width_box.setObjectName("scanWidthSpin")
        self.height_box.setObjectName("scanHeightSpin")
        for box, value in ((self.width_box, scan.width()), (self.height_box, scan.height())):
            box.setRange(120, 10000)
            box.setValue(value)
            box.setSuffix(" px")
        sizes = QHBoxLayout()
        sizes.addWidget(self.width_box)
        sizes.addWidget(QLabel("×"))
        sizes.addWidget(self.height_box)
        form.addRow("扫描框所在屏幕", self.screen_box)
        form.addRow("位置 X / Y", position)
        form.addRow("边框宽 × 高", sizes)
        self.panel_slider, self.panel_value = self._slider(panel_opacity)
        self.border_slider, self.border_value = self._slider(border_opacity)
        form.addRow("面板不透明度", self.panel_value)
        form.addRow("边框不透明度", self.border_value)
        layout.addLayout(form)
        self.pass_box = QCheckBox("锁定后，提示面板也允许鼠标穿透")
        self.pass_box.setObjectName("panelPassthroughCheck")
        self.pass_box.setChecked(panel_passthrough)
        layout.addWidget(self.pass_box)
        self.remote_box = QCheckBox("远程兼容（让远程桌面和录屏显示工具）")
        self.remote_box.setObjectName("remoteCompatibleCheck")
        self.remote_box.setToolTip("取图时短暂隐藏标记，避免工具图层进入识别；不会持续闪烁采集。")
        layout.addWidget(self.remote_box)
        hint = QLabel("穿透后按 Ctrl + Alt + L 恢复操作，也可从托盘恢复。\n"
                      "位置及尺寸采用界面像素；实际采集尺寸会随屏幕缩放换算。\n"
                      "请在窗口化或无边框游戏画面上框选团队面板。")
        hint.setWordWrap(True)
        self.recovery_hint = hint
        hint.setStyleSheet("color: #93a9ba; line-height: 1.5;")
        layout.addWidget(hint)
        actions = QHBoxLayout()
        reset = QPushButton("重置位置")
        reset.setObjectName("resetLayoutButton")
        reset.clicked.connect(self._reset)
        actions.addWidget(reset)
        actions.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

    def _screen_changed(self, index):
        name = self.screen_box.itemData(index)
        screen = next((screen for screen in self._screens if screen.name() == name), None)
        if screen is None:
            return
        area = screen.availableGeometry()
        self.x_box.setValue(area.left() + 60)
        self.y_box.setValue(area.top() + 80)

    @staticmethod
    def _slider(value):
        container = QHBoxLayout()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(20, 100)
        slider.setValue(round(value * 100))
        label = QLabel(f"{slider.value()}%")
        label.setFixedWidth(38)
        slider.valueChanged.connect(lambda v: label.setText(f"{v}%"))
        container.addWidget(slider)
        container.addWidget(label)
        return slider, container

    def _reset(self):
        self.reset_requested.emit()
        self.reject()
