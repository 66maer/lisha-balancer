"""Resizable, transparent scan guide. Geometry is in Qt logical pixels."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from .platform_win import exclude_from_capture, set_click_through
from .team_layout import Annotation, PlayerCell, TeamLayout


def bounded_rect(rect: QRect, bounds: QRect, minimum=(120, 120)) -> QRect:
    """Fit within one display, preserving size until that display is too small."""
    width = min(bounds.width(), max(minimum[0], rect.width()))
    height = min(bounds.height(), max(minimum[1], rect.height()))
    left = max(bounds.left(), min(rect.left(), bounds.right() - width + 1))
    top = max(bounds.top(), min(rect.top(), bounds.bottom() - height + 1))
    return QRect(left, top, width, height)


def resized_rect(start: QRect, delta: QPoint, edges: str, bounds: QRect,
                 minimum=(120, 120)) -> QRect:
    """Resize selected edges while anchoring the opposite edges."""
    result = QRect(start)
    if "l" in edges:
        result.setLeft(max(bounds.left(), min(start.left() + delta.x(),
                                              start.right() - minimum[0] + 1)))
    if "r" in edges:
        result.setRight(min(bounds.right(), max(start.right() + delta.x(),
                                               start.left() + minimum[0] - 1)))
    if "t" in edges:
        result.setTop(max(bounds.top(), min(start.top() + delta.y(),
                                            start.bottom() - minimum[1] + 1)))
    if "b" in edges:
        result.setBottom(min(bounds.bottom(), max(start.bottom() + delta.y(),
                                                 start.top() + minimum[1] - 1)))
    return result


class MarksOverlay(QWidget):
    """Independent, always input-transparent layer for cell annotations only."""

    def __init__(self, inset: int = 4):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setWindowTitle("戾煞配平 · 位置标记")
        self.setObjectName("marksOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.inset = inset
        self.excluded = False
        self.topmost = True
        self.annotations: list[Annotation] = []
        self.analysis_mode = "all"
        self.test_enabled = False
        self.calibration_enabled = False
        self.team_layout = TeamLayout()
        self._buff_icons: dict[str, QPixmap] = {}

    def showEvent(self, event):
        super().showEvent(event)
        self.excluded = exclude_from_capture(self)
        set_click_through(self, True)
        from .platform_win import set_topmost

        set_topmost(self, self.topmost)

    def set_annotations(self, annotations: list[Annotation]):
        if any(not isinstance(annotation, Annotation) for annotation in annotations):
            raise TypeError("Annotations must contain Annotation objects.")
        self.annotations = list(annotations)
        self.update()

    def set_analysis_mode(self, mode: str):
        if mode not in ("all", "fan", "circle"):
            raise ValueError("Analysis mode must be all, fan, or circle.")
        self.analysis_mode = mode
        self.update()

    def set_test_enabled(self, enabled: bool):
        self.test_enabled = bool(enabled)
        self.update()

    def set_team_layout(self, layout: TeamLayout):
        if not isinstance(layout, TeamLayout):
            raise TypeError("Team layout must be a TeamLayout object.")
        self.team_layout = layout
        self.update()

    def set_guides_visible(self, enabled: bool):
        self.calibration_enabled = bool(enabled)
        self.update()

    def displayed_annotations(self) -> list[Annotation]:
        annotations = self.annotations
        if self.test_enabled and not annotations:
            annotations = [
                Annotation("g1p2", "fan"), Annotation("g2p4", "fan"),
                Annotation("g1p2", "circle", 1), Annotation("g4p3", "circle", 2),
                Annotation("g5p4", "circle", 3),
            ]
        return [annotation for annotation in annotations
                if self.analysis_mode == "all" or annotation.kind == self.analysis_mode]

    def content_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(self.inset, self.inset, -self.inset, -self.inset)

    def _icon(self, kind: str, multi: bool = False) -> QPixmap:
        key = kind + ("_multi" if multi else "_one")
        if key not in self._buff_icons:
            filename = ("ui_red_3.png" if multi else "ui_red_1.png") if kind == "fan" else (
                "ui_black_2.png" if multi else "ui_black_1.png")
            roots = [Path(__file__).parent / "assets" / "buffs",
                     Path(__file__).resolve().parents[2] / "assets" / "buffs"]
            pixmap = QPixmap()
            for root in roots:
                if (root / filename).is_file():
                    pixmap.load(str(root / filename))
                    if not pixmap.isNull():
                        break
            self._buff_icons[key] = pixmap
        return self._buff_icons[key]

    def _draw_test_bounds(self, painter: QPainter, cells: list[PlayerCell]):
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for cell in cells:
            for rect, color in ((cell.rect, "#a5b8bd"), (cell.name_rect, "#60c5c5"),
                                (cell.buff_rect, "#c58ec7")):
                painter.setPen(QPen(QColor(color), 0.7, Qt.PenStyle.DashLine))
                painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

    def _draw_test_icons(self, painter: QPainter, cell: PlayerCell, annotations: list[Annotation]):
        kinds = list(dict.fromkeys(annotation.kind for annotation in annotations))
        area = cell.buff_rect.adjusted(1, 1, -1, -1)
        gap = 2.0
        size = min(18.0, area.height(), (area.width() - gap * (len(kinds) - 1)) / len(kinds))
        if size <= 0:
            return
        for index, kind in enumerate(kinds):
            multi = cell.group == 4 if kind == "fan" else any(a.order == 2 for a in annotations)
            icon = self._icon(kind, multi)
            if icon.isNull():
                continue
            target = QRectF(area.x() + index * (size + gap), area.y(), size, size)
            painter.drawPixmap(target, icon, QRectF(icon.rect()))

    def _draw_marks(self, painter: QPainter, cell: PlayerCell, annotations: list[Annotation]):
        unique = sorted(set(annotations), key=lambda annotation: (annotation.kind == "circle", annotation.order))
        area = cell.marker_rect
        gap = 3.0
        size = min(21.0, max(17.0, cell.rect.height() * 0.42), area.height(),
                   (area.width() - gap * (len(unique) - 1)) / len(unique))
        if size < 3:
            return
        left = area.right() - len(unique) * size - gap * (len(unique) - 1)
        top = area.center().y() - size / 2
        for index, annotation in enumerate(unique):
            box = QRectF(left + index * (size + gap), top, size, size)
            if annotation.kind == "fan":
                path = QPainterPath()
                path.moveTo(box.center().x(), box.bottom())
                path.arcTo(QRectF(box.left(), box.top(), box.width(), box.height() * 2), 30, 120)
                path.closeSubpath()
                painter.setBrush(QColor("#ed3e48"))
                painter.setPen(QPen(QColor("#ffb5b9"), 0.7))
                painter.drawPath(path)
            else:
                painter.setBrush(QColor("#080a0e"))
                center = getattr(annotation, "role", "helper") == "center"
                painter.setPen(QPen(QColor("#ffffff" if center else "#a6abb4"), 1.8 if center else 0.8))
                painter.drawEllipse(box.adjusted(0.5, 0.5, -0.5, -0.5))
                self._draw_order(painter, box, annotation.order)

    @staticmethod
    def _draw_order(painter: QPainter, box: QRectF, order: int):
        # Three small vector numerals remain legible even in Qt's offscreen
        # plugin, whose Windows build does not provide a font database.
        path = QPainterPath()
        if order == 1:
            path.moveTo(0.34, 0.31)
            path.lineTo(0.51, 0.20)
            path.lineTo(0.51, 0.80)
            path.moveTo(0.34, 0.80)
            path.lineTo(0.69, 0.80)
        elif order == 2:
            path.moveTo(0.29, 0.32)
            path.cubicTo(0.29, 0.08, 0.75, 0.10, 0.72, 0.35)
            path.cubicTo(0.69, 0.51, 0.31, 0.65, 0.29, 0.80)
            path.lineTo(0.74, 0.80)
        else:
            path.moveTo(0.29, 0.24)
            path.cubicTo(0.65, 0.08, 0.83, 0.42, 0.48, 0.47)
            path.cubicTo(0.85, 0.45, 0.83, 0.88, 0.29, 0.76)
        painter.save()
        painter.translate(box.topLeft())
        painter.scale(box.width(), box.height())
        painter.setPen(QPen(QColor("#ffffff"), 0.085, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.restore()

    def paintEvent(self, event):
        area = self.content_rect()
        if area.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_annotations(painter, area)

    def paint_annotations(self, painter, area, annotations=None):
        cells = self.team_layout.cells(area)
        if self.test_enabled or self.calibration_enabled:
            self._draw_test_bounds(painter, cells)
        by_player: dict[str, list[Annotation]] = defaultdict(list)
        for annotation in self.displayed_annotations() if annotations is None else annotations:
            by_player[annotation.player_key].append(annotation)
        for cell in cells:
            annotations = by_player.get(cell.key, [])
            if annotations:
                if self.test_enabled:
                    self._draw_test_icons(painter, cell, annotations)
                self._draw_marks(painter, cell, annotations)

    def annotated_frame(self, frame, annotations=None):
        result = frame.convertToFormat(QImage.Format.Format_RGB32)
        if result.isNull():
            return result
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.content_rect()
        if not area.isEmpty():
            painter.scale(result.width()/area.width(), result.height()/area.height())
            painter.translate(-area.x(), -area.y())
            self.paint_annotations(painter, area, annotations)
        painter.end()
        return result


class ScanOverlay(QWidget):
    geometry_changed = Signal()
    lock_requested = Signal()
    exclusion_changed = Signal(bool)

    INSET = 4
    HIT_MARGIN = 12

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setWindowTitle("戾煞配平 · 扫描框")
        self.setObjectName("scanOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setMinimumSize(120, 120)
        self.locked = False
        self.running = False
        self.border_opacity = 0.92
        self.excluded = False
        self.topmost = True
        self.markers = MarksOverlay(self.INSET)
        self._press_global: QPoint | None = None
        self._start_rect = QRect()
        self._edges = ""
        self._resize_bounds = QRect()
        self._update_mask()

    def border_region(self) -> QRegion:
        inner = self.rect().adjusted(self.HIT_MARGIN, self.HIT_MARGIN,
                                     -self.HIT_MARGIN, -self.HIT_MARGIN)
        return QRegion(self.rect()).subtracted(QRegion(inner))

    def _update_mask(self):
        # The center has no native window region at all: it passes input even
        # while the visible border remains editable.
        self.setMask(self.border_region())

    def _sync_markers(self):
        self.markers.setGeometry(self.geometry())
        if self.isVisible():
            self.markers.show()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_mask()
        self.excluded = exclude_from_capture(self)
        set_click_through(self, self.locked)
        from .platform_win import set_topmost

        set_topmost(self, self.topmost)
        self._sync_markers()
        self.exclusion_changed.emit(self.excluded and self.markers.excluded)

    def hideEvent(self, event):
        self.markers.hide()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.markers.close()
        super().closeEvent(event)

    def set_locked(self, locked: bool):
        self.locked = bool(locked)
        self._press_global = None
        self.unsetCursor()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.locked)
        set_click_through(self, self.locked)
        self.update()

    def set_running(self, running: bool):
        self.running = bool(running)
        self.update()

    def set_border_opacity(self, value: float):
        self.border_opacity = max(0.15, min(1.0, value))
        self.update()

    def set_topmost(self, enabled: bool):
        from .platform_win import set_topmost

        self.topmost = bool(enabled)
        self.markers.topmost = bool(enabled)
        set_topmost(self, self.topmost)
        set_topmost(self.markers, self.topmost)

    def set_annotations(self, annotations: list[Annotation]):
        self.markers.set_annotations(annotations)

    def set_analysis_mode(self, mode: str):
        self.markers.set_analysis_mode(mode)

    def set_test_enabled(self, enabled: bool):
        self.markers.set_test_enabled(enabled)

    def set_team_layout(self, layout: TeamLayout):
        self.markers.set_team_layout(layout)

    def set_guides_visible(self, enabled: bool):
        self.markers.set_guides_visible(enabled)

    def _hit_edges(self, point: QPoint) -> str:
        horizontal = "l" if point.x() < self.HIT_MARGIN else (
            "r" if point.x() >= self.width() - self.HIT_MARGIN else "")
        vertical = "t" if point.y() < self.HIT_MARGIN else (
            "b" if point.y() >= self.height() - self.HIT_MARGIN else "")
        if horizontal and vertical:
            return horizontal + vertical
        # Corner and midpoint grips resize. Other visible edge segments move
        # the frame, so moving never requires an interior input surface.
        if horizontal and abs(point.y() - self.height() / 2) <= 14:
            return horizontal
        if vertical and abs(point.x() - self.width() / 2) <= 14:
            return vertical
        return ""

    def _cursor_for(self, edges: str):
        if edges in ("lt", "rb"):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in ("rt", "lb"):
            return Qt.CursorShape.SizeBDiagCursor
        if edges in ("l", "r"):
            return Qt.CursorShape.SizeHorCursor
        if edges in ("t", "b"):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.SizeAllCursor

    def mousePressEvent(self, event: QMouseEvent):
        if (self.locked or event.button() != Qt.MouseButton.LeftButton
                or not self.border_region().contains(event.position().toPoint())):
            event.ignore()
            return
        self._press_global = event.globalPosition().toPoint()
        self._start_rect = self.geometry()
        self._edges = self._hit_edges(event.position().toPoint())
        screen = QApplication.screenAt(self._start_rect.center()) or self.screen()
        self._resize_bounds = screen.geometry()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.locked:
            return
        if self._press_global is None:
            self.setCursor(self._cursor_for(self._hit_edges(event.position().toPoint())))
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if self._edges:
            rect = resized_rect(self._start_rect, delta, self._edges, self._resize_bounds)
        else:
            rect = self._start_rect.translated(delta)
            screen = QApplication.screenAt(event.globalPosition().toPoint()) or self.screen()
            rect = bounded_rect(rect, screen.geometry())
        self.setGeometry(rect)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._press_global = None
        self.geometry_changed.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if not self.locked and event.button() == Qt.MouseButton.LeftButton:
            self.lock_requested.emit()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_markers()
        self.geometry_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_mask()
        self._sync_markers()
        self.geometry_changed.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Keep antialiased strokes a logical pixel away from the native ROI.
        # The border remains visible to desktop capture while recognition sees
        # only game pixels, including when lock changes its stroke/color.
        inset = self.INSET-1
        painter.setClipRegion(QRegion(self.rect()).subtracted(QRegion(
            self.rect().adjusted(inset, inset, -inset, -inset))))
        painter.setOpacity(self.border_opacity)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        color = QColor("#65dfcc" if self.locked else "#efc26b")
        painter.setPen(QPen(color, 1.4 if self.locked else 2.0))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 3, 3)
        painter.setPen(QPen(color, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        length = 18
        x0, y0, x1, y1 = 2, 2, self.width() - 3, self.height() - 3
        for x, y, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                              (x0, y1, 1, -1), (x1, y1, -1, -1)):
            painter.drawLine(x, y, x + dx * length, y)
            painter.drawLine(x, y, x, y + dy * length)
        if not self.locked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            for x, y in ((x0, (y0 + y1) // 2), (x1, (y0 + y1) // 2),
                          ((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1)):
                painter.drawRoundedRect(QRectF(x - 3, y - 6, 6, 12)
                                        if x in (x0, x1) else QRectF(x - 6, y - 3, 12, 6), 2, 2)
