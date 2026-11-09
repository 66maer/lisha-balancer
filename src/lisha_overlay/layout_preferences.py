"""Direct grid controls with a live, aspect-preserving calibration preview."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from .preferences import PreferencesDialog
from .team_layout import TeamLayout


class CalibrationPreview(QWidget):
    override_changed = Signal(str, object)
    tank_selected = Signal(str)

    def __init__(self, layout: TeamLayout, frame: QImage | None = None):
        super().__init__()
        self.team_layout = layout
        self.frame = frame.copy() if frame is not None else QImage()
        self.target = QRectF()
        self.setMinimumSize(240, 270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tank_key = None
        self.overrides = {}
        self.setToolTip("右键格子校正状态")

    def update_frame(self, image: QImage, metadata=None):
        self.frame = image.copy()
        self.update()

    def set_team_layout(self, layout):
        self.team_layout = layout
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0c141d"))
        source = self.frame.size() if not self.frame.isNull() else self.size()
        size = source.scaled(self.size() - self.size().__class__(16, 16), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(0, 0, size.width(), size.height())
        target.moveCenter(QRectF(self.rect()).center())
        self.target = target
        if not self.frame.isNull():
            painter.drawImage(target, self.frame)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for cell in self.team_layout.cells(target):
            for rect, color in ((cell.rect, "#b3c4cc"), (cell.name_rect, "#63d9cb"),
                                (cell.buff_rect, "#cf92df"), (cell.marker_rect, "#f1c779")):
                painter.setPen(QPen(QColor(color), 0.8, Qt.PenStyle.DashLine))
                painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            if self.frame.isNull():
                painter.setPen(QColor("#91a8b7"))
                painter.drawText(cell.rect, Qt.AlignmentFlag.AlignCenter, f"{cell.group}-{cell.slot}")
            if cell.key == self.tank_key:
                painter.setPen(QPen(QColor("#ffe28a"), 2))
                painter.drawRect(cell.rect.adjusted(1, 1, -1, -1))
            if cell.key in self.overrides:
                painter.fillRect(QRectF(cell.rect.topLeft(), cell.rect.size()).adjusted(2, 2, -cell.rect.width() + 6, -cell.rect.height() + 6),
                                 QColor("#f1c779"))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.RightButton or self.target.isEmpty():
            return super().mousePressEvent(event)
        cell = next((cell for cell in self.team_layout.cells(self.target)
                     if cell.rect.contains(event.position())), None)
        if cell is None:
            return
        menu = QMenu(self)
        for color, label in (("red", "赤"), ("black", "幽")):
            group = menu.addMenu(label)
            for layer in range(1, 5):
                group.addAction(str(layer), lambda checked=False, c=color, n=layer:
                                self.override_changed.emit(cell.key, {"color": c, "stacks": n, "alive": True}))
        menu.addAction("无 Buff", lambda: self.override_changed.emit(cell.key, {"color": "none", "stacks": 0, "alive": True}))
        menu.addAction("重伤", lambda: self.override_changed.emit(cell.key, {"color": "none", "stacks": 0, "alive": False}))
        isolate = menu.addAction("隔绝")
        isolate.setCheckable(True)
        isolate.setChecked(self.overrides.get(cell.key, {}).get("isolated", False))
        isolate.triggered.connect(lambda checked: self.override_changed.emit(cell.key, {"isolated": checked}))
        menu.addSeparator()
        menu.addAction("恢复自动", lambda: self.override_changed.emit(cell.key, None))
        menu.exec(event.globalPosition().toPoint())
        menu.deleteLater()


class LayoutPreferencesDialog(PreferencesDialog):
    layout_changed = Signal(object)

    def __init__(self, screens, scan, panel_opacity, border_opacity, panel_passthrough,
                 parent=None, *, team_layout=None, frame=None, tank_key=None,
                 snapshot_stage="before_tank", overrides=None, isolation_confirmed=False):
        super().__init__(screens, scan, panel_opacity, border_opacity, panel_passthrough, parent)
        self.setWindowTitle("戾煞 · 设置")
        self.setMinimumWidth(650)
        self._selected_layout = team_layout or TeamLayout()
        self._layout_initialized = False
        self._rects = {key: list(getattr(self._selected_layout, key))
                       for key in ("name_rect", "buff_rect", "marker_rect")}
        self._editing_rect = "buff_rect"
        self.overrides = {key: dict(value) for key, value in (overrides or {}).items()}
        base = self.layout()
        appearance = QWidget()
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(10, 12, 10, 12)
        while base.count() > 1:
            item = base.takeAt(0)
            if item.widget() is not None:
                appearance_layout.addWidget(item.widget())
            elif item.layout() is not None:
                item.layout().setParent(None)
                appearance_layout.addLayout(item.layout())
        tabs = QTabWidget()
        tabs.setObjectName("settingsTabs")
        tabs.setStyleSheet("QTabWidget::pane { border: 1px solid #334654; } "
                           "QTabBar::tab { background: #1b2a38; color: #a8bac8; padding: 6px 18px; } "
                           "QTabBar::tab:selected { color: #85ebd8; background: #29413e; }")
        grid_page = QWidget()
        grid_layout = QHBoxLayout(grid_page)
        grid_layout.setContentsMargins(10, 12, 10, 12)
        grid_layout.setSpacing(14)
        controls = QVBoxLayout()
        form = QFormLayout()
        form.setSpacing(7)
        options = self._selected_layout.grid_options
        self.columns_box = QSpinBox()
        self.columns_box.setObjectName("gridColumnsSpin")
        self.columns_box.setRange(1, 8)
        self.columns_box.setValue(sum(options["formation"]))
        self.formation_box = QComboBox()
        self.formation_box.setObjectName("gridFormationCombo")
        self.formation_box.setEditable(True)
        self._populate_formations(self.columns_box.value(), options["formation"])
        form.addRow("总列数 × 5 人", self.columns_box)
        form.addRow("排布", self.formation_box)
        self.grid_spins = {}
        for key, label in (("left", "左边距"), ("top", "上边距"), ("right", "右边距"),
                           ("bottom", "下边距"), ("column_gap", "列间距"),
                           ("row_gap", "组间距"), ("footer", "队名栏")):
            spin = self._percent_spin(options[key], key)
            self.grid_spins[key] = spin
            form.addRow(label, spin)
        self.tank_box = QComboBox()
        self.tank_box.setObjectName("tankCellCombo")
        self._fill_tanks(tank_key)
        self.tank_box.setParent(self)
        self.tank_box.hide()
        self.stage_box = QComboBox()
        self.stage_box.setObjectName("skillStageCombo")
        self.stage_box.addItem("一式前（T +3 赤）", "before_tank")
        self.stage_box.addItem("一式后", "after_tank")
        self.stage_box.setCurrentIndex(max(0, self.stage_box.findData(snapshot_stage)))
        self.stage_box.setParent(self)
        self.stage_box.hide()
        self.isolation_box = QCheckBox("隔绝已核对（本次）")
        self.isolation_box.setObjectName("isolationConfirmedCheck")
        self.isolation_box.setChecked(isolation_confirmed)
        form.addRow(self.isolation_box)
        form.takeRow(self.isolation_box)
        form.insertRow(2, self.isolation_box)
        controls.addLayout(form)
        self.validation_label = QLabel()
        self.validation_label.setStyleSheet("color: #f0b078; font-size: 10px;")
        controls.addWidget(self.validation_label)
        clear = QPushButton("清除格子校正")
        clear.setObjectName("clearOverridesButton")
        clear.clicked.connect(self._clear_overrides)
        controls.addWidget(clear)
        controls.addStretch()
        control_widget = QWidget()
        control_widget.setLayout(controls)
        scroll = QScrollArea()
        scroll.setObjectName("gridControlsScroll")
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(230)
        scroll.setMaximumWidth(250)
        scroll.setWidget(control_widget)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } "
                             "QScrollArea > QWidget > QWidget { background: #131e29; }")
        grid_layout.addWidget(scroll, 0)
        self.preview = CalibrationPreview(self._selected_layout, frame)
        self.preview.setObjectName("layoutPreview")
        self.preview.tank_key = None
        self.preview.overrides = self.overrides
        self.preview.tank_selected.connect(self._select_tank)
        self.preview.override_changed.connect(self._override_changed)
        self.tank_box.currentIndexChanged.connect(lambda: self._update_tank_preview())
        grid_layout.addWidget(self.preview, 1)
        tabs.addTab(grid_page, "团队布局")
        region_page = QWidget()
        region_form = QFormLayout(region_page)
        region_form.setContentsMargins(16, 16, 16, 16)
        self.rect_box = QComboBox()
        self.rect_box.addItem("Buff", "buff_rect")
        self.rect_box.addItem("姓名", "name_rect")
        self.rect_box.addItem("标记", "marker_rect")
        region_form.addRow("区域", self.rect_box)
        self.rect_spins = []
        for index, label in enumerate(("X", "Y", "宽", "高")):
            spin = self._percent_spin(self._rects[self._editing_rect][index], f"roi{index}", maximum=100)
            self.rect_spins.append(spin)
            region_form.addRow(label, spin)
            spin.valueChanged.connect(self._rect_edited)
        region_form.addRow(QLabel("相对于单个人员格子的比例"))
        tabs.addTab(region_page, "格内区域")
        tabs.addTab(appearance, "外观")
        base.insertWidget(0, tabs)
        self.columns_box.valueChanged.connect(self._columns_changed)
        self.formation_box.currentTextChanged.connect(self._rebuild)
        for spin in self.grid_spins.values():
            spin.valueChanged.connect(self._rebuild)
        self.rect_box.currentIndexChanged.connect(self._switch_rect)
        self._rebuild()
        self.layout().activate()
        self.adjustSize()
        self.resize(650, 540)

    @staticmethod
    def _percent_spin(value, name, maximum=45):
        spin = QDoubleSpinBox()
        spin.setObjectName(name + "PercentSpin")
        spin.setRange(0, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(0.25)
        spin.setSuffix(" %")
        spin.setValue(value * 100)
        return spin

    def _populate_formations(self, total, selected=None):
        self.formation_box.blockSignals(True)
        self.formation_box.clear()
        patterns = [(total,)]
        if total == 5:
            patterns += [(3, 2), (2, 3), (1, 4), (4, 1)]
        else:
            patterns += [(i, total - i) for i in range(1, total)]
        current = tuple(selected or (total,))
        if current not in patterns:
            patterns.append(current)
        self.formation_box.addItems(["+".join(map(str, pattern)) for pattern in patterns])
        self.formation_box.setCurrentText("+".join(map(str, current)))
        self.formation_box.blockSignals(False)

    def _columns_changed(self, total):
        current_tank = self.tank_box.currentData()
        self._populate_formations(total)
        self._fill_tanks(current_tank)
        self._rebuild()

    def _fill_tanks(self, selected):
        self.tank_box.blockSignals(True)
        self.tank_box.clear()
        self.tank_box.addItem("—", None)
        for group in range(1, self.columns_box.value() + 1):
            for slot in range(1, 6):
                self.tank_box.addItem(f"{group} 队 · {slot}", f"g{group}p{slot}")
        self.tank_box.setCurrentIndex(max(0, self.tank_box.findData(selected)))
        self.tank_box.blockSignals(False)

    def _select_tank(self, key):
        index = self.tank_box.findData(key)
        if index >= 0:
            self.tank_box.setCurrentIndex(index)

    def _update_tank_preview(self):
        self.preview.tank_key = None
        self.preview.update()

    def _override_changed(self, key, value):
        if value is None:
            self.overrides.pop(key, None)
        else:
            self.overrides[key] = {**self.overrides.get(key, {}), **value}
        self.preview.overrides = self.overrides
        self.preview.update()

    def _clear_overrides(self):
        self.overrides.clear()
        self.preview.update()

    def _switch_rect(self):
        self._editing_rect = self.rect_box.currentData()
        for spin, value in zip(self.rect_spins, self._rects[self._editing_rect]):
            spin.blockSignals(True)
            spin.setValue(value * 100)
            spin.blockSignals(False)

    def _rect_edited(self):
        self._rects[self._editing_rect] = [spin.value() / 100 for spin in self.rect_spins]
        self._rebuild(preserve_groups=True)

    def _rebuild(self, *, preserve_groups=False):
        try:
            text = self.formation_box.currentText().replace(" ", "").replace("＋", "+")
            formation = tuple(int(value) for value in text.split("+"))
            if sum(formation) != self.columns_box.value():
                raise ValueError("排布合计应等于总列数")
            layout = TeamLayout.uniform(formation,
                                        **{key: spin.value() / 100 for key, spin in self.grid_spins.items()},
                                        **self._rects)
        except (ValueError, TypeError) as error:
            self.validation_label.setText(str(error))
            self.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        if not self._layout_initialized:
            # Detected columns may have individual exact boundaries. Opening
            # settings alone must not replace them with an approximate grid.
            layout = self._selected_layout
        elif preserve_groups:
            layout = TeamLayout(groups=self._selected_layout.groups,
                                grid_options=self._selected_layout.grid_options, **self._rects)
        if self._layout_initialized and layout != self._selected_layout:
            self.overrides.clear()
            self.isolation_box.setChecked(False)
        self._selected_layout = layout
        self._layout_initialized = True
        self.validation_label.clear()
        self.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        self.preview.set_team_layout(layout)
        self.layout_changed.emit(layout)

    @property
    def selected_layout(self):
        return self._selected_layout

    def update_frame(self, image, metadata=None):
        self.preview.update_frame(image, metadata)
