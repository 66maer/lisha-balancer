"""A temporary, capture-excluded cell picker with no global input hooks."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .platform_win import exclude_from_capture, set_click_through
from .team_layout import PlayerCell, TeamLayout


class TankPicker(QWidget):
    """Own input only during an explicit selection session.

    Geometry and inset use Qt logical pixels, matching ``ScanOverlay.geometry``.
    Selection emits a stable grid key, never a mouse action against the game.
    """

    tank_selected = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None, *, inset: int = 4):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        if isinstance(inset, bool) or not isinstance(inset, int) or inset < 0:
            raise ValueError("选择框内边距必须是非负整数。")
        self.setObjectName("tankPicker")
        self.setWindowTitle("戾煞 · 点击格子指定T")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.inset = inset
        self.excluded = False
        self.team_layout: TeamLayout | None = None
        self._cells: tuple[PlayerCell, ...] = ()
        self._selecting = False
        self._tank_key: str | None = None
        self._hover_key: str | None = None

    @property
    def is_selecting(self) -> bool:
        return self._selecting

    @property
    def current_tank(self) -> str | None:
        return self._tank_key

    def content_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(self.inset, self.inset, -self.inset, -self.inset)

    def set_grid(self, layout: TeamLayout, geometry: QRect, inset: int | None = None) -> None:
        if not isinstance(layout, TeamLayout):
            raise TypeError("选择T需要有效的团队布局。")
        if not isinstance(geometry, QRect):
            raise TypeError("选择框位置应为 Qt 逻辑像素 QRect。")
        value = self.inset if inset is None else inset
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("选择框内边距必须是非负整数。")
        if geometry.width() <= 2 * value or geometry.height() <= 2 * value:
            raise ValueError("选择框内没有可用的团队区域。")
        self.inset = value
        self.team_layout = layout
        self.setGeometry(QRect(geometry))
        self._update_cells()
        self._hover_key = None
        self.set_current_tank(self._tank_key)

    def set_current_tank(self, player_key: str | None) -> None:
        self._tank_key = player_key if any(cell.key == player_key for cell in self._cells) else None
        self.update()

    def _update_cells(self) -> None:
        if self.team_layout is not None and not self.content_rect().isEmpty():
            self._cells = tuple(self.team_layout.cells(self.content_rect()))

    def cell_at(self, position: QPoint | QPointF) -> str | None:
        point = QPointF(position)
        return next((cell.key for cell in self._cells if cell.rect.contains(point)), None)

    def start_selection(self) -> bool:
        if self.team_layout is None or not self._cells:
            raise ValueError("请先设置团队布局和扫描框位置。")
        self._selecting = True
        self._hover_key = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.show()
        if not self.excluded:
            self.cancel_selection()
            return False
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.update()
        return self._selecting

    def cancel_selection(self) -> None:
        self._finish(None)

    def _finish(self, player_key: str | None) -> None:
        active = self._selecting
        self._selecting = False
        self._hover_key = None
        self.hide()
        if not active:
            return
        if player_key is None:
            self.cancelled.emit()
        else:
            self._tank_key = player_key
            self.tank_selected.emit(player_key)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.excluded = exclude_from_capture(self)
        set_click_through(self, not self._selecting)

    def hideEvent(self, event) -> None:
        active = self._selecting
        self._selecting = False
        self._hover_key = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        super().hideEvent(event)
        if active:
            self.cancelled.emit()

    def closeEvent(self, event) -> None:
        self.cancel_selection()
        event.accept()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and getattr(self, "_selecting", False):
            self.cancel_selection()
        return super().event(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "team_layout"):
            self._update_cells()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._selecting:
            event.ignore()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel_selection()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._finish(self.cell_at(event.position()))
        else:
            event.ignore()
            return
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._selecting:
            return
        key = self.cell_at(event.position())
        if key != self._hover_key:
            self._hover_key = key
            self.setCursor(Qt.CursorShape.PointingHandCursor if key else Qt.CursorShape.ArrowCursor)
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._selecting and event.key() == Qt.Key.Key_Escape:
            self.cancel_selection()
            event.accept()
        else:
            event.ignore()

    def paintEvent(self, event) -> None:
        if not self._selecting:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Windows layered windows pass alpha-zero pixels through. A 1/255
        # hit plane lets the entire cell be selected without a visible backdrop.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for cell in self._cells:
            current = cell.key == self._tank_key
            hovered = cell.key == self._hover_key
            color = QColor("#f0cf86" if current else "#63dfcb" if hovered else "#a6bac6")
            color.setAlpha(230 if current else 180 if hovered else 90)
            painter.setPen(QPen(color, 1.6 if current else 1.1 if hovered else .7))
            painter.drawRect(cell.rect.adjusted(.7, .7, -.7, -.7))
