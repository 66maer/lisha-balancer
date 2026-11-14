"""Minimal game overlay and a separate diagnostic frame preview."""

from __future__ import annotations

from datetime import datetime
from html import escape
import re
from typing import Any

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent, QIcon, QImage, QMouseEvent, QPainter, QPen, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .difficulty import SAFE_TOLERANCE, settlement_level

__all__ = ["ControlPanel", "PreviewWindow"]

_MUTED = "#8996aa"
_TEAL = "#63dfcb"
_YELLOW = "#e8c47a"

_STYLE = """
QWidget { color: #e8edf5; font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif; font-size: 12px; }
QLabel { background: transparent; border: none; }
QLabel#panelTitle { color: #d0dde7; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
QLabel#summaryLabel { color: #9eafbe; font-size: 11px; }
QPushButton { background: #192432; border: 1px solid #2c3a4e; border-radius: 7px; padding: 6px 8px; font-size: 11px; }
QPushButton:hover { background: #223145; border-color: #52627b; }
QPushButton:pressed { background: #111b28; }
QPushButton:disabled { color: #536177; background: #141d29; border-color: #222e3f; }
QToolButton { background: transparent; border: none; border-radius: 4px; padding: 0; }
QToolButton:hover { background: #2a3947; }
QToolButton:pressed { background: #18252f; }
QToolButton:checked { background: #1d3a37; }
QToolButton#testButton:checked { background: #3a3222; }
QToolButton#attachButton[attached="true"] { background: #1a2c34; }
QToolButton#tankButton { color: #9cabb9; font-size: 11px; font-weight: 700; }
QToolButton#tankButton[selecting="true"] { color: #f3d38a; background: #3a3222; }
QToolButton:disabled { color: #52687a; background: transparent; }
QMessageBox { background: #101923; }
QWidget#modeSegments { background: #0e1720; border: 1px solid #2c3b48; border-radius: 5px; }
QToolButton[modeSegment="true"] { color: #8fa3b5; border-radius: 3px; font-size: 11px; }
QToolButton[modeSegment="true"]:checked { color: #9af0df; background: #25413e; }
QToolTip { color: #d3e0e9; background: #18232e; border: 1px solid #3c4d5e; padding: 5px; }
QMenu { color: #dce7ef; background: #14202b; border: 1px solid #3a4d5d; padding: 4px; }
QMenu::item { color: #dce7ef; background: transparent; padding: 5px 22px 5px 12px; border-radius: 3px; }
QMenu::item:selected { color: #c5fff0; background: #294b44; }
QMenu::item:disabled { color: #63798b; background: transparent; }
QMenu::separator { height: 1px; background: #304453; margin: 4px 6px; }
QFileDialog { color: #e0eaf2; background: #101923; }
QFileDialog QFrame, QFileDialog QStackedWidget { color: #e0eaf2; background: #101923; }
QFileDialog QLabel { color: #d8e5ee; background: transparent; }
QFileDialog QLineEdit, QFileDialog QComboBox {
    color: #e4eef5; background: #192a38; border: 1px solid #385062;
    border-radius: 4px; padding: 5px; selection-color: #e8fff8; selection-background-color: #2a6257;
}
QFileDialog QLineEdit:focus, QFileDialog QComboBox:focus { border-color: #63bbaa; }
QFileDialog QLineEdit:disabled, QFileDialog QComboBox:disabled { color: #647b8f; background: #14212c; }
QFileDialog QComboBox::drop-down { background: #243b4b; border-left: 1px solid #385062; width: 18px; }
QFileDialog QAbstractItemView, QFileDialog QTreeView, QFileDialog QListView {
    color: #d7e5ef; background: #0f1923; alternate-background-color: #162632;
    border: 1px solid #2f4454; selection-color: #d4fff2; selection-background-color: #285046;
    outline: 0; show-decoration-selected: 1;
}
QFileDialog QWidget#qt_scrollarea_viewport { color: #d7e5ef; background: #0f1923; }
QFileDialog QAbstractItemView::item { padding: 3px; border: none; }
QFileDialog QAbstractItemView::item:selected { color: #d4fff2; background: #285046; }
QFileDialog QAbstractItemView::item:disabled { color: #63798b; }
QFileDialog QHeaderView { color: #cbdde9; background: #1b2d3b; }
QFileDialog QHeaderView::section {
    color: #cbdde9; background: #1b2d3b; padding: 5px;
    border: none; border-right: 1px solid #354b5a; border-bottom: 1px solid #354b5a;
}
QFileDialog QToolButton { color: #d7e5ef; background: #1b2d3b; border: 1px solid #344b5b; padding: 3px; }
QFileDialog QToolButton:hover { background: #294353; }
QFileDialog QToolButton:disabled { color: #5d7486; background: #14212c; border-color: #263a49; }
QFileDialog QSplitter::handle { background: #2d4251; }
QFileDialog QScrollBar { background: #101923; border: none; }
QFileDialog QScrollBar::handle { background: #3b5365; border-radius: 3px; min-height: 18px; min-width: 18px; }
QFileDialog QScrollBar::add-page, QFileDialog QScrollBar::sub-page { background: #101923; }
"""


def _icon(kind: str, color: str = "#aebcd0") -> QIcon:
    """Draw small, native icons without a font or image-file dependency."""
    canvas = QPixmap(18, 18)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if kind == "play":
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF

        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(6, 4), QPointF(13, 9), QPointF(6, 14)]))
    elif kind == "pause":
        painter.drawLine(6, 5, 6, 13)
        painter.drawLine(12, 5, 12, 13)
    elif kind in ("lock", "unlock"):
        painter.drawRoundedRect(QRectF(4, 8, 10, 7), 1.5, 1.5)
        painter.drawArc(QRectF(6, 2.5, 6, 9), 0 if kind == "lock" else 20 * 16, 180 * 16 if kind == "lock" else 150 * 16)
        if kind == "lock":
            painter.drawLine(6, 6, 6, 8)
            painter.drawLine(12, 6, 12, 8)
    elif kind == "attach":
        painter.drawRoundedRect(QRectF(2.5, 4, 6.5, 10), 1, 1)
        painter.drawRoundedRect(QRectF(11, 4, 4.5, 10), 1, 1)
    elif kind == "detach":
        painter.drawRoundedRect(QRectF(2.5, 7, 8, 8), 1, 1)
        painter.drawLine(8, 10, 15, 3)
        painter.drawLine(10, 3, 15, 3)
        painter.drawLine(15, 3, 15, 8)
    elif kind == "pin":
        painter.drawLine(6, 3, 12, 3)
        painter.drawLine(7, 3, 7, 7)
        painter.drawLine(11, 3, 11, 7)
        painter.drawLine(7, 7, 4, 11)
        painter.drawLine(11, 7, 14, 11)
        painter.drawLine(4, 11, 14, 11)
        painter.drawLine(9, 11, 9, 16)
    elif kind == "test":
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.moveTo(7, 3)
        path.lineTo(7, 7)
        path.lineTo(3.5, 13)
        path.quadTo(3, 15, 5, 15)
        path.lineTo(13, 15)
        path.quadTo(15, 15, 14, 13)
        path.lineTo(11, 7)
        path.lineTo(11, 3)
        painter.drawPath(path)
        painter.drawLine(6, 3, 12, 3)
        painter.drawLine(6, 11, 12, 11)
    elif kind == "settings":
        from math import cos, pi, sin

        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF

        teeth = []
        for index in range(32):
            angle = index * pi / 16 - pi / 2
            radius = 6.2 if index % 4 in (1, 2) else 4.8
            teeth.append(QPointF(9 + radius * cos(angle), 9 + radius * sin(angle)))
        painter.drawPolygon(QPolygonF(teeth))
        painter.drawEllipse(QRectF(6.8, 6.8, 4.4, 4.4))
    elif kind == "preview":
        painter.drawRoundedRect(QRectF(2, 3, 14, 11), 1.5, 1.5)
        painter.drawLine(6, 16, 12, 16)
        painter.drawLine(9, 14, 9, 16)
    elif kind == "grid":
        painter.drawRoundedRect(QRectF(2.5, 2.5, 13, 13), 1.5, 1.5)
        for position in (7, 11):
            painter.drawLine(position, 3, position, 15)
            painter.drawLine(3, position, 15, position)
    elif kind == "save":
        painter.drawRoundedRect(QRectF(3, 2.5, 12, 13), 1.5, 1.5)
        painter.drawRect(QRectF(6, 2.5, 6, 4.5))
        painter.drawRect(QRectF(6, 10, 6, 5.5))
    elif kind == "close":
        painter.drawLine(5, 5, 13, 13)
        painter.drawLine(13, 5, 5, 13)
    elif kind == "minus":
        painter.drawLine(5, 10, 13, 10)
    elif kind == "help":
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.moveTo(5.5, 5.5)
        path.cubicTo(5.5, 1.5, 12.5, 1.5, 12.5, 5.5)
        path.cubicTo(12.5, 8, 9, 8, 9, 11)
        painter.drawPath(path)
        painter.drawPoint(9, 15)
    painter.end()
    return QIcon(canvas)


class _Dot(QWidget):
    def __init__(self, color: str = _MUTED, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(12, 12)

    def set_color(self, color: str) -> None:
        self.color = QColor(color)
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        halo = QColor(self.color)
        halo.setAlpha(35)
        painter.setBrush(halo)
        painter.drawEllipse(QRectF(0, 0, 12, 12))
        painter.setBrush(self.color)
        painter.drawEllipse(QRectF(3, 3, 6, 6))


class _DragGrip(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(8, 12)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#687d8c"))
        for x in (1.5, 5.5):
            for y in (2.5, 6.5, 10.5):
                painter.drawEllipse(QRectF(x - 0.7, y - 0.7, 1.4, 1.4))


def _buff_badge(color: str, stacks: int, *, plus: bool = False) -> QPixmap:
    canvas = QPixmap(32, 32)
    canvas.setDevicePixelRatio(2)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#de6277' if color == 'red' else '#6f7c89'), 0.7))
    painter.setBrush(QColor('#b8233f' if color == 'red' else '#12171d'))
    painter.drawEllipse(QRectF(0.5, 0.5, 15, 15))
    font = painter.font()
    font.setPixelSize(11)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor('#ffffff'))
    painter.drawText(QRectF(0, 0, 16, 16), Qt.AlignmentFlag.AlignCenter, str(stacks))
    if plus:
        font.setPixelSize(7)
        painter.setFont(font)
        painter.drawText(QRectF(10.8, 2, 5, 10), Qt.AlignmentFlag.AlignCenter, '+')
    painter.end()
    return canvas


def _image_source(image: QPixmap) -> str:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return 'data:image/png;base64,' + bytes(buffer.data().toBase64()).decode('ascii')


class _CommandLabel(QLabel):
    """Fit one rich command inside its fixed row without wrapping."""

    def __init__(self):
        super().__init__()
        self.document = QTextDocument(self)
        self.document.setDocumentMargin(0)
        self.setFixedHeight(22)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setStyleSheet('font-size: 11px; font-weight: 700;')

    def setText(self, text: str) -> None:
        super().setText(text)
        self.document.setDefaultFont(self.font())
        color = self.palette().windowText().color().name()
        self.document.setHtml(f'<span style="color:{color}">{text}</span>')
        self.update()

    def paintEvent(self, event: Any) -> None:
        bounds = self.contentsRect()
        size = self.document.size()
        scale = min(1., bounds.width() / max(1., size.width()), bounds.height() / max(1., size.height()))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(bounds.left(), bounds.top() + (bounds.height() - size.height() * scale) / 2)
        painter.scale(scale, scale)
        self.document.drawContents(painter)


class _DifficultyButton(QToolButton):
    def paintEvent(self, event: Any) -> None:
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = ''
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        font = self.font()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor('#64788b' if self.isChecked() else '#9af0df'))
        painter.drawText(QRect(1, 0, 20, self.height()), Qt.AlignmentFlag.AlignCenter, '普')
        painter.setPen(QColor('#64788b'))
        painter.drawText(QRect(19, 0, 10, self.height()), Qt.AlignmentFlag.AlignCenter, '/')
        painter.setPen(QColor('#f0cd7b' if self.isChecked() else '#64788b'))
        painter.drawText(QRect(27, 0, 20, self.height()), Qt.AlignmentFlag.AlignCenter, '英')


class ControlPanel(QWidget):
    """Compact toolbar; state and native window behavior belong to the controller."""

    mode_changed = Signal(str)
    difficulty_changed = Signal(str)
    test_toggled = Signal(bool)
    pin_toggled = Signal(bool)
    grid_requested = Signal()
    tank_requested = Signal()
    attach_toggled = Signal()
    close_requested = Signal()
    position_changed = Signal()
    size_changed = Signal()

    # Retained for controller compatibility; none create visible controls.
    capture_toggled = Signal()
    analysis_toggled = Signal()
    lock_toggled = Signal()
    preview_requested = Signal()
    save_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("controlPanel")
        self.setWindowTitle("戾煞配平助手")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setStyleSheet(_STYLE)
        self.setFixedSize(360, 178)
        self._drag_offset: QPoint | None = None
        self._mode = "all"
        self._difficulty = 'normal'
        self._test_mode = False
        self._pinned = True
        self._attached = False
        self._running = False
        self._locked = False
        self._frame_available = False
        self._grid_busy = False
        self._tank_selecting = False
        self._grid_ready = False
        self._analysis_active = False
        self._region: dict[str, Any] = {}
        self._metadata: dict[str, Any] = {}
        self._screen_name = ""
        self._summary_text = "待识别"
        self._summary_error = False
        self._summary_statistics = None
        self._summary_reference = False
        self._buff_counts = None
        self._instruction_text = ''
        self._command_lines = ['', '', '']
        self._final_statistics = None
        self._status_detail = ""
        self._hotkey_status = ""
        self._lock_shortcut = "Ctrl+Alt+L"
        self._hide_shortcut = "Ctrl+Alt+H"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self._header = QWidget(self)
        self._header.setObjectName("dragHandle")
        self._header.setFixedHeight(24)
        self._header.setCursor(Qt.CursorShape.SizeAllCursor)
        self._header.installEventFilter(self)
        header = QHBoxLayout(self._header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        header.addWidget(_DragGrip())
        title = QLabel("戾煞配平助手")
        title.setObjectName("panelTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(title)
        header.addStretch(1)
        self.about_button = self._tool_button("aboutButton", "help")
        self.about_button.setToolTip("版本信息与作者")
        self.about_button.setAccessibleName("版本信息与作者")
        self.about_button.clicked.connect(self._show_about)
        header.addWidget(self.about_button)
        self.settings_button = self._tool_button("settingsButton", "settings")
        self.settings_button.setToolTip("设置")
        self.settings_button.clicked.connect(lambda _checked=False: self.settings_requested.emit())
        self.test_button = self._tool_button("testButton", "test", checkable=True)
        self.attach_button = self._tool_button("attachButton", "detach")
        self.pin_button = self._tool_button("pinButton", "pin", checkable=True)
        close = self._tool_button("closeButton", "close")
        close.setToolTip("关闭配平助手")
        self.test_button.toggled.connect(self._test_clicked)
        self.attach_button.clicked.connect(lambda _checked=False: self.attach_toggled.emit())
        self.pin_button.toggled.connect(self._pin_clicked)
        close.clicked.connect(lambda _checked=False: self.close_requested.emit())
        for button in (self.settings_button, self.test_button, self.attach_button):
            button.setParent(self)
            button.hide()
        for button in (self.pin_button, close):
            header.addWidget(button)
        layout.addWidget(self._header)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QToolButton] = {}
        segments = QWidget()
        segments.setObjectName("modeSegments")
        segments.setFixedHeight(24)
        segment_layout = QHBoxLayout(segments)
        segment_layout.setContentsMargins(2, 2, 2, 2)
        segment_layout.setSpacing(2)
        for mode, text, name, tip in (
            ("all", "扇＋圈", "modeAllButton", "扇形与圈联合修正"),
            ("circle", "仅圈", "modeCircleButton", "仅使用圈修正"),
        ):
            button = QToolButton()
            button.setObjectName(name)
            button.setText(text)
            button.setToolTip(tip)
            button.setProperty("modeSegment", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._mode_group.addButton(button)
            button.clicked.connect(lambda _checked=False, value=mode: self._mode_clicked(value))
            self.mode_buttons[mode] = button
            segment_layout.addWidget(button, 1)
        segment_layout.addSpacing(3)
        self.difficulty_button = _DifficultyButton()
        self.difficulty_button.setObjectName('difficultyButton')
        self.difficulty_button.setText('普/英')
        self.difficulty_button.setCheckable(True)
        self.difficulty_button.setFixedSize(48, 20)
        self.difficulty_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.difficulty_button.toggled.connect(self._difficulty_clicked)
        segment_layout.addWidget(self.difficulty_button)
        self.start_button = self._tool_button("analysisStartButton", "play")
        self.start_button.setToolTip("开始识别")
        self.start_button.clicked.connect(lambda _checked=False: self.analysis_toggled.emit())
        segment_layout.addWidget(self.start_button)
        self.grid_button = self._tool_button("gridButton", "grid")
        self.grid_button.setToolTip("自动定位团队格子")
        self.grid_button.clicked.connect(lambda _checked=False: self.grid_requested.emit())
        self.tank_button = QToolButton()
        self.tank_button.setObjectName("tankButton")
        self.tank_button.setFixedSize(20, 20)
        self.tank_button.setText("T")
        self.tank_button.setToolTip("点击格子指定T")
        self.tank_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tank_button.clicked.connect(lambda _checked=False: self.tank_requested.emit())
        segment_layout.addWidget(self.grid_button)
        # Kept only for old controller/API callers; live strategy has no T step.
        self.tank_button.setParent(self)
        self.tank_button.hide()
        layout.addWidget(segments)

        self._buff_sources = {}
        for color in ('red', 'black'):
            for stacks in range(1, 6):
                key = f'{color}{stacks}'
                icon = _buff_badge(color, stacks)
                self._buff_sources[key] = _image_source(icon)
        check = QPixmap(20, 20)
        check.setDevicePixelRatio(2)
        check.fill(Qt.GlobalColor.transparent)
        painter = QPainter(check)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#087f45'), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(2, 5, 4, 7)
        painter.drawLine(4, 7, 8, 2)
        painter.end()
        self._check_source = _image_source(check)

        self._summary_area = QWidget()
        self._summary_area.setObjectName('summaryArea')
        self._summary_area.setFixedHeight(40)
        summary_layout = QHBoxLayout(self._summary_area)
        summary_layout.setContentsMargins(3, 2, 3, 2)
        summary_layout.setSpacing(6)
        status_rows = QVBoxLayout()
        status_rows.setSpacing(0)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(2)
        self._summary_label = QLabel()
        self._summary_label.setObjectName("summaryLabel")
        self._summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self._summary_label.setFixedHeight(18)
        self._summary_label.setMinimumWidth(0)
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        summary_row.addWidget(self._summary_label, 1)
        self._reference_label = QLabel('参考')
        self._reference_label.setObjectName('referenceLabel')
        self._reference_label.setStyleSheet('color: #8a5700; font-size: 11px; font-weight: 700;')
        self._reference_label.hide()
        summary_row.addWidget(self._reference_label, 0, Qt.AlignmentFlag.AlignRight)
        status_rows.addLayout(summary_row)
        self._buff_counts_row = QWidget()
        self._buff_counts_row.setObjectName('buffCountsRow')
        self._buff_counts_row.setFixedHeight(18)
        buffs_layout = QHBoxLayout(self._buff_counts_row)
        buffs_layout.setContentsMargins(0, 0, 0, 0)
        buffs_layout.setSpacing(6)
        self._buff_count_labels = {}
        for color in ('red', 'black'):
            for stacks in range(1, 4):
                key = f'{color}{stacks}'
                group = QHBoxLayout()
                group.setSpacing(2)
                icon_label = QLabel()
                icon_label.setFixedSize(16, 16)
                icon_label.setPixmap(_buff_badge(color, stacks, plus=stacks == 3))
                count_label = QLabel('—')
                count_label.setObjectName(f'{key}Count')
                count_label.setFixedWidth(14)
                group.addWidget(icon_label)
                group.addWidget(count_label)
                buffs_layout.addLayout(group)
                self._buff_count_labels[key] = count_label
        buffs_layout.addStretch(1)
        status_rows.addWidget(self._buff_counts_row)
        summary_layout.addLayout(status_rows, 1)
        separator = QWidget()
        separator.setObjectName('settlementSeparator')
        separator.setFixedSize(1, 30)
        separator.setStyleSheet('background: #b5c1cb;')
        summary_layout.addWidget(separator)
        self._result_label = QLabel()
        self._result_label.setObjectName('settlementLabel')
        self._result_label.setFixedSize(88, 36)
        self._result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result_label.setTextFormat(Qt.TextFormat.RichText)
        self._result_label.setStyleSheet('font-size: 10px;')
        summary_layout.addWidget(self._result_label)
        layout.addWidget(self._summary_area)
        self._command_area = QWidget()
        self._command_area.setObjectName('commandArea')
        self._command_area.setFixedHeight(66)
        command_rows = QVBoxLayout(self._command_area)
        command_rows.setContentsMargins(0, 0, 0, 0)
        command_rows.setSpacing(0)
        self._instruction_labels = []
        for name in ('instructionLabel', 'guideInstructionLabel', 'circleInstructionLabel'):
            label = _CommandLabel()
            label.setObjectName(name)
            command_rows.addWidget(label)
            self._instruction_labels.append(label)
        self._instruction_label = self._instruction_labels[0]
        layout.addWidget(self._command_area)

        # Existing capture/save callers may still address this attribute. It is
        # deliberately outside the layout and never exposed on the toolbar.
        self.save_button = QPushButton(self)
        self.save_button.setObjectName("saveButton")
        self.save_button.setEnabled(False)
        self.save_button.hide()
        self.save_button.clicked.connect(lambda _checked=False: self.save_requested.emit())

        self.set_mode("all")
        self.set_difficulty('normal')
        self.set_test_state(False)
        self.set_pinned(True)
        self.set_attached(False)
        self._sync_selection_actions()
        layout.activate()
        self._render_summary()
        self._refresh_tooltip()

    @staticmethod
    def _tool_button(name: str, kind: str, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        button.setObjectName(name)
        button.setFixedSize(20, 20)
        button.setIconSize(QSize(16, 16))
        button.setIcon(_icon(kind))
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _show_about(self) -> None:
        QMessageBox.about(
            self, "版本信息", f"戾煞配平助手\n版本 {__version__}\n\nby 华乾·丐箩箩"
        )

    @staticmethod
    def _button(text: str, name: str, signal: Any, icon_kind: str | None = None) -> QPushButton:
        """Standard button factory used only by the diagnostic preview."""
        button = QPushButton(text)
        button.setObjectName(name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(30)
        if icon_kind:
            button.setIcon(_icon(icon_kind))
            button.setIconSize(QSize(16, 16))
        button.clicked.connect(lambda _checked=False: signal.emit())
        return button

    @staticmethod
    def _check_silently(button: QToolButton, checked: bool) -> None:
        blocked = button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(blocked)

    def _mode_clicked(self, mode: str) -> None:
        self.set_mode(mode)
        self.mode_changed.emit(mode)

    def _test_clicked(self, enabled: bool) -> None:
        self.set_test_state(enabled)
        self.test_toggled.emit(enabled)

    def _pin_clicked(self, pinned: bool) -> None:
        self.set_pinned(pinned)
        self.pin_toggled.emit(pinned)

    def set_mode(self, mode: str | None) -> None:
        self._mode = mode if mode in self.mode_buttons else None
        self._mode_group.setExclusive(False)
        for value, button in self.mode_buttons.items():
            button.setChecked(value == self._mode)
        self._mode_group.setExclusive(True)

    def _difficulty_clicked(self, heroic: bool) -> None:
        self.set_difficulty('heroic' if heroic else 'normal')
        self.difficulty_changed.emit(self._difficulty)

    def set_difficulty(self, difficulty: str) -> None:
        self._difficulty = difficulty if difficulty in SAFE_TOLERANCE else 'normal'
        self._check_silently(self.difficulty_button, self._difficulty == 'heroic')
        name = '英雄' if self._difficulty == 'heroic' else '普通'
        self.difficulty_button.setToolTip(f'当前{name}，安全人数差 ≤ {SAFE_TOLERANCE[self._difficulty]}；点击切换普/英')
        self.difficulty_button.setAccessibleName(f'难度切换，当前{name}')
        self.difficulty_button.update()
        self._render_summary()

    def set_grid_busy(self, busy: bool) -> None:
        self._grid_busy = bool(busy)
        self._sync_selection_actions()

    def set_analysis_active(self, active: bool) -> None:
        self._analysis_active = bool(active)
        if active:self._drag_offset=None
        self._header.setCursor(Qt.CursorShape.ArrowCursor if active else Qt.CursorShape.SizeAllCursor)
        self.start_button.setIcon(_icon("pause" if active else "play"))
        self._sync_selection_actions()

    def set_grid_ready(self, ready: bool) -> None:
        self._grid_ready=bool(ready)
        self._sync_selection_actions()

    def set_tank_selecting(self, selecting: bool) -> None:
        self._tank_selecting = bool(selecting)
        self.tank_button.setProperty("selecting", self._tank_selecting)
        self.tank_button.style().unpolish(self.tank_button)
        self.tank_button.style().polish(self.tank_button)
        self._sync_selection_actions()

    def _sync_selection_actions(self) -> None:
        analyze_enabled = not self._grid_busy and not self._tank_selecting
        self.grid_button.setEnabled(analyze_enabled and not self._analysis_active)
        self.start_button.setEnabled(analyze_enabled and (self._grid_ready or self._analysis_active))
        self.start_button.setToolTip('停止识别' if self._analysis_active else
                                    '开始识别' if self._grid_ready else '请先校准网格')
        self.tank_button.setEnabled(not self._grid_busy)
        self.difficulty_button.setEnabled(analyze_enabled)
        for button in self.mode_buttons.values():
            button.setEnabled(analyze_enabled)

    def set_test_state(self, enabled: bool) -> None:
        changed = self._test_mode != bool(enabled)
        self._test_mode = bool(enabled)
        self._check_silently(self.test_button, self._test_mode)
        self.test_button.setIcon(_icon("test", _YELLOW if enabled else "#9cabb9"))
        self.test_button.setToolTip("关闭测试显示" if enabled else "开启测试显示")
        if changed:
            self.set_summary("测试" if enabled else "待识别")
        else:
            self._render_summary()
        self._refresh_tooltip()

    def set_pinned(self, pinned: bool) -> None:
        # The controller applies native window flags and renews capture exclusion.
        self._pinned = bool(pinned)
        self._check_silently(self.pin_button, self._pinned)
        self.pin_button.setIcon(_icon("pin", _TEAL if pinned else "#8294a4"))
        self.pin_button.setToolTip("取消置顶" if pinned else "置顶")

    def set_summary(self, text: str, error: bool = False) -> None:
        self._summary_text = str(text or "待识别").replace("\n", " ").strip()
        self._summary_error = bool(error)
        self._summary_statistics = None
        self._summary_reference = False
        self._buff_counts = None
        self._instruction_text = ''
        self._command_lines = ['', '', '']
        self._final_statistics = None
        self._render_summary()
        self._refresh_tooltip()

    def set_result(self, result) -> None:
        self.set_summary(result.summary, result.status == 'no_solution')
        self._summary_reference = result.reference
        self._buff_counts = result.buff_counts
        self._instruction_text = result.instruction
        lines = result.instruction.splitlines()
        self._command_lines = (lines + ['', '', ''])[:3]
        if len(lines) > 3:
            self._command_lines[2] = '，'.join(lines[2:])
        if result.final_red is not None and result.final_black is not None:
            self._final_statistics = (result.final_red, result.final_black)
        if result.current_red is not None and result.current_black is not None:
            self._summary_statistics = (result.current_red, result.current_black)
        self._render_summary()
        self._refresh_tooltip()

    def _render_summary(self) -> None:
        if not hasattr(self, "_summary_label"):
            return
        if self._summary_statistics is not None:
            red,black=self._summary_statistics
            delta=red-black
            self._summary_label.setTextFormat(Qt.TextFormat.RichText)
            self._summary_area.setStyleSheet('QWidget#summaryArea { background: #dce4e9; border-radius: 3px; }')
            self._summary_label.setStyleSheet('font-size: 11px;')
            self._summary_label.setText(f'<b style="color:#b8233f">红 {red}</b>&nbsp;&nbsp;'
                f'<b style="color:#12171d">黑 {black}</b>&nbsp;&nbsp;'
                + self._difference_html(delta))
        else:
            self._summary_area.setStyleSheet('')
            self._summary_label.setTextFormat(Qt.TextFormat.PlainText)
            color = "#f29a9e" if self._summary_error else (_YELLOW if self._test_mode else "#a8bccb")
            self._summary_label.setStyleSheet(f"color: {color}; font-size: 11px;")
            available = max(1, self._summary_label.width())
            self._summary_label.setText(self._summary_label.fontMetrics().elidedText(
                self._summary_text, Qt.TextElideMode.ElideRight, available))
        self._reference_label.setVisible(self._summary_reference)
        self._reference_label.setStyleSheet(
            f"color: {'#8a5700' if self._summary_statistics is not None else _YELLOW}; font-size: 11px; font-weight: 700;"
        )
        for key, label in self._buff_count_labels.items():
            count = self._buff_counts.get(key, 0) if self._buff_counts is not None else None
            if self._buff_counts is not None and key.endswith('3'):
                count = sum(self._buff_counts.get(f'{key[:-1]}{stacks}', 0) for stacks in range(3, 6))
            label.setText(str(count) if count is not None else '—')
            label.setStyleSheet(
                f"color: {'#26333e' if self._summary_statistics is not None else '#a8bccb'}; font-size: 11px;"
            )
        if hasattr(self, '_instruction_labels'):
            color = '#f29a9e' if self._summary_error else '#dce6ed'
            for label, text in zip(self._instruction_labels, self._command_lines):
                label.setStyleSheet(f'color: {color}; font-size: 11px; font-weight: 700;')
                label.setText(self._instruction_html(text))
            final = '<span style="color:#52616c; font-size:9px">处理后</span>'
            if self._final_statistics is None:
                final += '<br><span style="color:#52616c">—</span>'
            else:
                red, black = self._final_statistics
                final += (f' <b style="color:#b8233f">红 {red}</b> <b style="color:#12171d">黑 {black}</b><br>'
                          + self._difference_html(red - black))
            self._result_label.setText(final)

    def _difference_html(self, difference: int) -> str:
        level = settlement_level(difference, self._difficulty)
        color = '#b8233f' if difference > 0 else '#12171d'
        mark = {'safe': f'<img src="{self._check_source}" width="10" height="10" alt="✓" style="vertical-align:middle" />',
                'danger': '<b style="color:#c37c00">危</b>',
                'fatal': '<b style="color:#b8233f">死</b>'}[level]
        signed = f'{difference:+d}' if difference else '0'
        return f'<b style="color:{color}">差 {signed}</b>&nbsp;{mark}'

    def _instruction_html(self, text: str) -> str:
        def badge(match):
            color, stacks, count = match.groups()
            digit = int(stacks) if stacks.isdecimal() else '一二三四五'.index(stacks) + 1
            key = f"{'red' if color == '红' else 'black'}{digit}"
            return (f'<img src="{self._buff_sources[key]}" width="16" height="16" style="vertical-align:middle" />'
                    + (f' × {count[1:]}' if count else ''))

        rich = escape(text).replace('\n', '<br>')
        return re.sub(r'[\[{｛](红|黑)([1-5一二三四五])(×\d+)?[\]}｝]', badge, rich)

    def set_capture_state(self, running: bool) -> None:
        self._running = bool(running)
        self._refresh_tooltip()

    def set_locked(self, locked: bool) -> None:
        # Locking concerns the scan guide; it must not disable panel dragging.
        self._locked = bool(locked)
        self._refresh_tooltip()

    def set_attached(self, attached: bool) -> None:
        self._attached = bool(attached)
        self.attach_button.setProperty("attached", self._attached)
        self.attach_button.setIcon(_icon("detach" if attached else "attach", _TEAL if attached else "#9cabb9"))
        self.attach_button.setToolTip("分离提示面板，独立拖动" if attached else "贴靠扫描框，随选区移动")
        self.attach_button.style().unpolish(self.attach_button)
        self.attach_button.style().polish(self.attach_button)
        self._refresh_tooltip()

    def set_capture_info(self, region: dict, screen_name: str = "") -> None:
        self._region = dict(region or {})
        self._screen_name = str(screen_name or "")
        self._refresh_tooltip()

    def set_frame_info(self, metadata: dict) -> None:
        self._metadata = dict(metadata or {})
        self.set_frame_available(True)

    def set_frame_available(self, available: bool) -> None:
        self._frame_available = bool(available)
        self.save_button.setEnabled(self._frame_available)
        if not available:
            self._metadata = {}
        self._refresh_tooltip()

    def set_status(self, message: str, error: bool = False) -> None:
        self._status_detail = str(message or "")
        short = self._status_detail.strip()
        if error:
            self.set_summary("无解" if "无解" in self._status_detail else "异常", True)
        elif short and len(short) <= 16 and not any(mark in short for mark in "\n，。；！？"):
            self.set_summary(short)
        else:
            # Operational instructions belong in tooltips, never the compact row.
            self._refresh_tooltip()

    def set_hotkey_status(self, text: str) -> None:
        self._hotkey_status = str(text or "")
        self._refresh_tooltip()

    def set_hotkey_bindings(self, lock_shortcut: str, hide_shortcut: str) -> None:
        self._lock_shortcut = str(lock_shortcut)
        self._hide_shortcut = str(hide_shortcut)
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        if not hasattr(self, "_summary_label"):
            return
        details = [self._summary_text]
        if self._summary_reference:details.append('参考：按本帧模型候选计算，尚有识别疑点或正在校验连续帧。')
        if self._instruction_text:details.append(self._instruction_text)
        if self._test_mode:
            details.append("测试显示：示例内容不代表真实 Buff 识别")
        details.append(("按需取图 · 远程兼容" if self.property("onDemandCapture") else "采集运行中 · 5 Hz")
                       if self._running else "采集已暂停")
        if self._region:
            width = self._region.get("width", self._region.get("w", 0))
            height = self._region.get("height", self._region.get("h", 0))
            left = self._region.get("left", self._region.get("x", 0))
            top = self._region.get("top", self._region.get("y", 0))
            details.append(f"选区 {width} × {height} px · 位置 ({left}, {top})")
        if self._screen_name:
            details.append("屏幕 " + self._screen_name)
        elapsed = self._metadata.get("elapsed_ms", self._metadata.get("capture_ms"))
        if isinstance(elapsed, (int, float)):
            details.append(f"捕获 {elapsed:.1f} ms")
        details.append("扫描框已锁定" if self._locked else "扫描框可调整")
        if self._status_detail:
            details.append(self._status_detail)
        details.append(self._lock_shortcut + " 锁定 / 解锁；" + self._hide_shortcut + " 隐藏 / 恢复")
        if self._hotkey_status:
            details.append(self._hotkey_status)
        tooltip = "\n".join(details)
        self.setToolTip(tooltip)
        self._summary_label.setToolTip(tooltip)
        for label in self._instruction_labels:
            label.setToolTip(tooltip)
        self._result_label.setToolTip(tooltip)
        self._header.setToolTip("拖动提示面板\n" + tooltip)

    def sizeHint(self) -> QSize:
        return QSize(360, 178)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._render_summary()
        self.size_changed.emit()

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        if watched is self._header and isinstance(event, QMouseEvent):
            if self._analysis_active:
                self._drag_offset=None
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                if not event.buttons() & Qt.MouseButton.LeftButton:
                    self._drag_offset = None
                    return False
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                self.position_changed.emit()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = None
                return True
        return super().eventFilter(watched, event)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(73, 96, 111, 170), 1))
        painter.setBrush(QColor(16, 25, 35, 232))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.close_requested.emit()


class _ImageCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self.setMinimumSize(260, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def update_frame(self, image: QImage) -> None:
        self._image = image.copy()
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#090f18"))
        if self._image.isNull():
            painter.setPen(QColor("#9aa9bd"))
            painter.drawText(self.rect().adjusted(12, 12, -12, -12), Qt.AlignmentFlag.AlignCenter, "等待首帧画面\n开始采集后显示扫描框内的真实画面")
            return
        available = self.rect().adjusted(10, 10, -10, -10)
        size = self._image.size().scaled(available.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(0, 0, size.width(), size.height())
        target.moveCenter(QRectF(available).center())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self._image)


class PreviewWindow(QWidget):
    save_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("previewWindow")
        self.setWindowTitle("扫描区域预览 · 戾煞配平助手")
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setStyleSheet(_STYLE + "\nQWidget#previewWindow { background: #101923; }")
        self.resize(760, 540)
        self.setMinimumSize(400, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        toolbar = QHBoxLayout()
        self._dot = _Dot()
        toolbar.addWidget(self._dot)
        self._state = QLabel("实时预览 · 已暂停")
        self._state.setStyleSheet("font-weight: 600;")
        toolbar.addWidget(self._state, 1)
        self._save_button = ControlPanel._button("保存原始截图", "previewSaveButton", self.save_requested, "save")
        self._save_button.setEnabled(False)
        toolbar.addWidget(self._save_button)
        layout.addLayout(toolbar)
        self._canvas = _ImageCanvas()
        self._canvas.setObjectName("previewCanvas")
        layout.addWidget(self._canvas, 1)
        self._frame_label = QLabel("尚未收到画面")
        self._frame_label.setStyleSheet("color: #c0ccdc; font-size: 11px;")
        layout.addWidget(self._frame_label)
        hint = QLabel("按原始比例显示 · 保存时使用原始分辨率 · 暂停后保留最近一帧")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7e8da3; font-size: 10px;")
        layout.addWidget(hint)

    def update_frame(self, image: QImage, metadata: dict) -> None:
        if image.isNull():
            return
        self._canvas.update_frame(image)
        metadata = metadata or {}
        width = metadata.get("width", image.width())
        height = metadata.get("height", image.height())
        parts = [f"{width} × {height} px"]
        elapsed = metadata.get("elapsed_ms", metadata.get("capture_ms", metadata.get("latency_ms")))
        if isinstance(elapsed, (int, float)):
            parts.append(f"捕获 {elapsed:.1f} ms")
        captured_at = metadata.get("captured_at")
        if isinstance(captured_at, (int, float)):
            try:
                parts.append(datetime.fromtimestamp(captured_at).strftime("%H:%M:%S"))
            except (ValueError, OverflowError, OSError):
                pass
        elif isinstance(captured_at, datetime):
            parts.append(captured_at.strftime("%H:%M:%S"))
        self._frame_label.setText("   ·   ".join(parts))
        self._save_button.setEnabled(True)

    def clear_frame(self) -> None:
        self._canvas.update_frame(QImage())
        self._frame_label.setText("选区已更新，等待新画面")
        self._save_button.setEnabled(False)

    def set_capture_state(self, running: bool) -> None:
        self._state.setText("实时预览 · 正在采集" if running else "实时预览 · 已暂停")
        self._dot.set_color(_TEAL if running else _MUTED)
