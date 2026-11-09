"""Application controller for two independent, optionally docked overlay windows."""
from __future__ import annotations

from copy import deepcopy
from ctypes import wintypes
from datetime import datetime
import faulthandler
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import (
    QAbstractNativeEventFilter, QObject, QPoint, QRect, QThread, QTimer, Qt,
    Signal, Slot, QMetaObject, QSize, QCoreApplication, QEvent, QStandardPaths,
)
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from .capture import CaptureWorker
from . import __version__
from .analysis import AnalysisResult, demo_result
from .live_strategy import evaluate_current as evaluate
from .overlay import ScanOverlay, bounded_rect
from .panel import ControlPanel, PreviewWindow
from .team_layout import TeamLayout
from .auto_capture import AutoGridWorker
from .tank_picker import TankPicker
from .platform_win import (
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, WM_HOTKEY, exclude_from_capture,
    native_client_region, register_hotkey, set_click_through, set_topmost, unregister_hotkey,
    set_remote_compatible, flush_compositor,
)
from .layout_preferences import LayoutPreferencesDialog
from .processing import AnalysisWorker
from .settings import load_settings, save_settings, settings_path
from .snapshot_confirmation import SnapshotConfirmation
from .support import record_environment, export_bundle

LOG = logging.getLogger(__name__)


def _icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#142631"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 15, 15)
    painter.setPen(QPen(QColor("#63dfcb"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for x, y, dx, dy in ((16, 16, 1, 1), (48, 16, -1, 1),
                          (16, 48, 1, -1), (48, 48, -1, -1)):
        painter.drawLine(x, y, x + dx * 11, y)
        painter.drawLine(x, y, x, y + dy * 11)
    painter.setBrush(QColor("#efc26b"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(28, 28, 8, 8)
    painter.end()
    return QIcon(pixmap)


def _setting_float(settings: dict, key: str, default: float) -> float:
    value = settings.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return default
    return max(0.2, min(1.0, float(value)))


def _read_rect(value) -> QRect | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        return None
    if any(abs(v) > 100000 for v in value) or value[2] <= 0 or value[3] <= 0:
        return None
    return QRect(*value)


def _rect_list(rect: QRect) -> list[int]:
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def _requested_command(argv: list[str]) -> str:
    if "--quit" in argv:
        return "quit"
    if "--locate" in argv:
        return "locate"
    if "--tank" in argv:
        index = argv.index("--tank")
        keys = {f"g{group}p{slot}" for group in range(1, 9) for slot in range(1, 6)}
        if index + 1 >= len(argv) or argv[index + 1] not in keys:
            raise ValueError("--tank 需要有效格子编号，例如 g1p1。")
        return "tank:" + argv[index + 1]
    if "--analyze" in argv:
        index = argv.index("--analyze")
        if index + 1 >= len(argv) or argv[index + 1] not in ("all", "fan", "circle"):
            raise ValueError("--analyze 需要 all、fan 或 circle。")
        return "analyze:" + argv[index + 1]
    return "restore"


def panel_placement(scan: QRect, size: QSize, available: QRect,
                    preferred: QPoint | None = None) -> QRect | None:
    """Keep the toolbar completely outside the captured game data."""
    gap = 4
    width, height = size.width(), size.height()
    candidates = [
        QRect(scan.left(), scan.top() - height - gap, width, height),
        QRect(scan.right() + gap + 1, scan.top(), width, height),
        QRect(scan.left(), scan.bottom() + gap + 1, width, height),
        QRect(scan.left() - width - gap, scan.top(), width, height),
    ]
    if preferred is not None:
        candidates.insert(0, QRect(preferred, size))
    candidates = [bounded_rect(rect, available, (width, height)) for rect in candidates]
    valid = [rect for rect in candidates if available.contains(rect) and not rect.intersects(scan)]
    if not valid:
        return None
    if preferred is not None:
        valid.sort(key=lambda r: (r.x() - preferred.x()) ** 2 + (r.y() - preferred.y()) ** 2)
    return valid[0]


class HotkeyFilter(QAbstractNativeEventFilter):
    LOCK_ID = 0x4D10
    HIDE_ID = 0x4D11

    def __init__(self, lock, hide):
        super().__init__()
        self.callbacks = {self.LOCK_ID: lock, self.HIDE_ID: hide}
        mods = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
        self.registered = {}
        self.labels = {}
        self.used_fallback = False
        for key, letter in ((self.LOCK_ID, "L"), (self.HIDE_ID, "H")):
            success = register_hotkey(key, mods, ord(letter))
            label = f"Ctrl+Alt+{letter}"
            if not success:
                success = register_hotkey(key, mods | MOD_SHIFT, ord(letter))
                if success:
                    label = f"Ctrl+Alt+Shift+{letter}"
                    self.used_fallback = True
            self.registered[key] = success
            self.labels[key] = label

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and int(msg.wParam) in self.callbacks:
                QTimer.singleShot(0, self.callbacks[int(msg.wParam)])
                return True, 0
        return False, 0

    def close(self):
        for key, registered in self.registered.items():
            if registered:
                unregister_hotkey(key)


class OverlayController(QObject):
    start_requested = Signal(dict)
    stop_requested = Signal()
    region_requested = Signal(dict)
    analysis_requested = Signal(QImage, dict, dict)
    grid_requested = Signal(dict)
    snapshot_requested = Signal(dict)

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.app = app
        self.settings = load_settings()
        self.remote_compatible = True
        set_remote_compatible(self.remote_compatible)
        self._remote_hidden = []
        self._remote_capture_kind = None
        self._remote_capture_id = 0
        self._remote_open_preferences = False
        self.running = False
        self.locked = False
        self.attached = self.settings.get("attached", True) is not False
        self.panel_passthrough = self.settings.get("panel_passthrough", False) is True
        self.panel_opacity = _setting_float(self.settings, "panel_opacity", 0.96)
        self.border_opacity = _setting_float(self.settings, "border_opacity", 0.92)
        self.pinned = self.settings.get("pinned", True) is not False
        self.analysis_mode = self.settings.get("analysis_mode", "all")
        if self.analysis_mode not in ("all", "fan", "circle"):
            self.analysis_mode = "all"
        self.test_enabled = False
        self.tank_key = None
        self.snapshot_stage = 'after_tank'
        self.manual_overrides = {}
        self.isolation_confirmed = False
        self._processing_busy = False
        self._recognition_previous = None
        self._analysis_attempts = 0
        self._generation = 0
        self._players = []
        self._last_result = None
        self._analysis_pending = False
        self._continuous_analysis = False
        self._stable_frames = 0
        self._last_diagnostic_signature = None
        self._team_confirmation = SnapshotConfirmation()
        self._confirmed_team_result = None
        self._analysis_started_at = None
        self._first_result_ms = None
        self._grid_busy = False
        self._grid_generation = 0
        self._auto_applying = False
        self._pick_after_grid = False
        self._analyze_after_grid = None
        self.grid_ready = False
        self._calibrated_geometry = None
        try:
            self.team_layout = TeamLayout.from_dict(self.settings.get("team_layout", {}))
        except (ValueError, TypeError, KeyError):
            self.team_layout = TeamLayout()
        valid_tank_keys = {f"g{group.group}p{slot}" for group in self.team_layout.groups for slot in range(1, 6)}
        if not isinstance(self.tank_key, str) or self.tank_key not in valid_tank_keys:
            self.tank_key = None
        self._placing_panel = False
        self._hidden = False
        self._quitting = False
        self._region: dict = {}
        self._frame = QImage()
        self._analysis_frame = QImage()
        self._published_frame = QImage()
        self.debug_captures = '--debug-captures' in sys.argv and '--no-debug-captures' not in sys.argv
        self.prefer_exact = self.settings.get('prefer_exact', False) is True
        self._metadata: dict = {}
        self._dialog = None

        LOG.info("Startup stage: create overlay widgets begin")
        self.scan = ScanOverlay()
        self.panel = ControlPanel()
        self.preview = PreviewWindow()
        self.tank_picker = TankPicker(inset=self.scan.INSET)
        self.tank_picker.tank_selected.connect(self._tank_selected)
        self.tank_picker.cancelled.connect(self._tank_selection_cancelled)
        LOG.info("Startup stage: create overlay widgets end")
        self.scan.setWindowIcon(app.windowIcon())
        self.panel.setWindowIcon(app.windowIcon())
        self.preview.setWindowIcon(app.windowIcon())
        self.panel.setWindowOpacity(self.panel_opacity)
        self.scan.set_border_opacity(self.border_opacity)
        self.panel.set_attached(self.attached)
        self.panel.set_locked(False)
        self.panel.set_mode(self.analysis_mode)
        self.panel.set_pinned(self.pinned)
        self.scan.set_team_layout(self.team_layout)
        self.scan.set_analysis_mode(self.analysis_mode)
        self._place_initial()
        self._avoid_panel_overlap()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.persist)
        self._region_timer = QTimer(self)
        self._region_timer.setSingleShot(True)
        self._region_timer.setInterval(50)
        self._region_timer.timeout.connect(self._update_region)
        self._grid_feedback_timer = QTimer(self)
        self._grid_feedback_timer.setSingleShot(True)
        self._grid_feedback_timer.setInterval(2000)
        self._grid_feedback_timer.timeout.connect(self._end_grid_feedback)
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(250)
        self._topmost_timer.timeout.connect(self._refresh_topmost)

        self.scan.geometry_changed.connect(self._scan_moved)
        self.scan.lock_requested.connect(self.toggle_lock)
        self.panel.position_changed.connect(self._panel_moved)
        self.panel.size_changed.connect(lambda: QTimer.singleShot(0,self._panel_resized))
        self.panel.capture_toggled.connect(self.toggle_capture)
        self.panel.lock_toggled.connect(self.toggle_lock)
        self.panel.attach_toggled.connect(self.toggle_attach)
        self.panel.preview_requested.connect(self.open_preview)
        self.panel.save_requested.connect(self.save_frame)
        self.panel.settings_requested.connect(self.open_preferences)
        self.panel.grid_requested.connect(self.locate_grid)
        self.panel.tank_requested.connect(self.select_tank)
        self.panel.close_requested.connect(self.quit)
        self.panel.mode_changed.connect(self.select_analysis_mode)
        self.panel.analysis_toggled.connect(self.toggle_analysis)
        self.panel.test_toggled.connect(self.set_test_enabled)
        self.panel.pin_toggled.connect(self.set_pinned)
        self.panel.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.panel.customContextMenuRequested.connect(self._open_context_menu)
        self.preview.save_requested.connect(self.save_frame)

        self.thread = QThread(self)
        self.thread.setObjectName("screenCaptureThread")
        self.worker = CaptureWorker()
        self.worker.moveToThread(self.thread)
        self.start_requested.connect(self.worker.start_capture)
        self.snapshot_requested.connect(self.worker.capture_once)
        self.stop_requested.connect(self.worker.stop_capture)
        self.region_requested.connect(self.worker.set_region)
        self.worker.frame_ready.connect(self._on_frame, Qt.ConnectionType.QueuedConnection)
        self.worker.failed.connect(self._on_capture_error, Qt.ConnectionType.QueuedConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

        self.analysis_thread = QThread(self)
        self.analysis_thread.setObjectName("recognitionAndPlanningThread")
        self.analysis_worker = AnalysisWorker()
        LOG.info("Startup stage: preload image libraries and templates begin")
        self.analysis_worker.prepare(self.team_layout.to_dict(), self.tank_key)
        LOG.info("Startup stage: preload image libraries and templates end")
        self.analysis_worker.moveToThread(self.analysis_thread)
        self.analysis_requested.connect(self.analysis_worker.process)
        self.analysis_worker.completed.connect(self._analysis_completed, Qt.ConnectionType.QueuedConnection)
        self.analysis_worker.failed.connect(self._analysis_failed, Qt.ConnectionType.QueuedConnection)
        self.analysis_thread.finished.connect(self.analysis_worker.deleteLater)
        self.grid_worker = AutoGridWorker()
        self.grid_worker.moveToThread(self.analysis_thread)
        self.grid_requested.connect(self.grid_worker.locate)
        self.grid_worker.completed.connect(self._grid_completed, Qt.ConnectionType.QueuedConnection)
        self.grid_worker.failed.connect(self._grid_failed, Qt.ConnectionType.QueuedConnection)
        self.analysis_thread.finished.connect(self.grid_worker.deleteLater)
        self.analysis_thread.start()
        self._continuous_timer = QTimer(self)
        self._continuous_timer.setInterval(40)
        self._continuous_timer.timeout.connect(self._continuous_tick)
        self._continuous_timer.start()
        self.panel.set_mode(self.analysis_mode)

        self.hotkeys = HotkeyFilter(self.toggle_lock, self.toggle_visibility)
        self.app.installNativeEventFilter(self.hotkeys)
        self.panel.set_hotkey_bindings(self.hotkeys.labels[HotkeyFilter.LOCK_ID],
                                      self.hotkeys.labels[HotkeyFilter.HIDE_ID])
        failed = []
        if not self.hotkeys.registered[HotkeyFilter.LOCK_ID]:
            failed.append("Ctrl+Alt+L")
        if not self.hotkeys.registered[HotkeyFilter.HIDE_ID]:
            failed.append("Ctrl+Alt+H")
        if failed:
            self.panel.set_hotkey_status("快捷键被占用：" + "、".join(failed) + "；可从托盘恢复。")
        elif self.hotkeys.used_fallback:
            self.panel.set_hotkey_status("默认快捷键被占用，已启用上方备用组合。")
        LOG.info("Startup stage: setup tray begin")
        self._setup_tray()
        LOG.info("Startup stage: setup tray end")
        app.screenRemoved.connect(self._screen_removed)
        app.aboutToQuit.connect(self.shutdown)
        LOG.info("Startup stage: scan.show begin")
        self.scan.show()
        LOG.info("Startup stage: scan.show end")
        LOG.info("Startup stage: panel.show begin")
        self.panel.show()
        LOG.info("Startup stage: panel.show end")
        LOG.info("Startup stage: set_pinned begin")
        self.set_pinned(self.pinned)
        LOG.info("Startup stage: set_pinned end")
        LOG.info("Startup stage: exclude capture windows begin")
        self._exclude_windows()
        self._refresh_tank_button()
        self._refresh_summary()
        LOG.info("Startup stage: exclude capture windows end")
        self._record_grid('needs_calibration',{})
        QTimer.singleShot(150, self._update_region)

    def _place_initial(self):
        screen = self.app.primaryScreen()
        area = screen.availableGeometry()
        saved = _read_rect(self.settings.get("scan_geometry"))
        if saved is not None:
            screen = self.app.screenAt(saved.center()) or screen
            area = screen.availableGeometry()
        else:
            saved = QRect(area.left() + 80, area.top() + 110, 360, min(480, area.height() - 160))
        # Automatic alignment can place only the transparent border outside a
        # monitor so a team panel flush against its edge stays fully captured.
        keep_detected = (getattr(self, "grid_ready", False) and saved.width() >= 120 and saved.height() >= 120
                         and screen.geometry().contains(saved.adjusted(self.scan.INSET, self.scan.INSET,
                                                                       -self.scan.INSET, -self.scan.INSET)))
        self.scan.setGeometry(saved if keep_detected else bounded_rect(saved, area))
        panel_rect = _read_rect(self.settings.get("panel_geometry"))
        if panel_rect and not self.attached:
            panel_screen = self.app.screenAt(panel_rect.center()) or screen
            panel_rect.setSize(QSize(self.panel.width(), self.panel.sizeHint().height()))
            self.panel.move(bounded_rect(panel_rect, panel_screen.availableGeometry()).topLeft())
        else:
            self._dock_panel()

    def _dock_panel(self):
        if not self.attached or self._placing_panel:
            return
        self._placing_panel = True
        try:
            frame = self.scan.geometry()
            area = self.scan.screen().availableGeometry()
            width, height = self.panel.width(), self.panel.sizeHint().height()
            choice = panel_placement(frame, QSize(width, height), area)
            if choice is None:
                reserve = min(height + 4, max(0, area.height() - 120))
                self.scan.setGeometry(bounded_rect(frame, area.adjusted(0, reserve, 0, 0)))
                choice = panel_placement(self.scan.geometry(), QSize(width, height), area)
            if choice is not None:
                self.panel.move(choice.topLeft())
        finally:
            self._placing_panel = False

    def _scan_moved(self):
        if self._quitting:
            return
        if not getattr(self, "_auto_applying", False):
            self._cancel_grid_request()
            self._cancel_tank_selection()
            if self.scan.geometry()!=getattr(self,'_calibrated_geometry',None):
                self.grid_ready=False
                self.panel.set_grid_ready(False)
        self._dock_panel()
        if not self.attached:
            self._avoid_panel_overlap()
        self._region_timer.start()
        self._save_timer.start()

    def _panel_moved(self):
        if self._placing_panel:
            return
        # Dragging the panel is also a direct way to detach it.
        if self.attached:
            self.attached = False
            self.panel.set_attached(False)
        # Keep at least the title bar on a connected monitor.
        screen = self.app.screenAt(self.panel.geometry().center())
        if screen is None:
            screen = self.app.screenAt(self.panel.pos()) or self.app.primaryScreen()
            self._placing_panel = True
            self.panel.move(bounded_rect(self.panel.geometry(), screen.availableGeometry(),
                                        (self.panel.width(), self.panel.height())).topLeft())
            self._placing_panel = False
        self._avoid_panel_overlap()
        self._save_timer.start()

    def _panel_resized(self):
        if self._quitting or self._placing_panel:
            return
        if self.attached:
            self._dock_panel()
        else:
            self._avoid_panel_overlap()
        self._save_timer.start()

    def _avoid_panel_overlap(self):
        if self._placing_panel or not self.panel.geometry().intersects(self.scan.geometry()):
            return
        screen = self.app.screenAt(self.panel.geometry().center()) or self.scan.screen()
        placement = panel_placement(self.scan.geometry(), self.panel.size(),
                                    screen.availableGeometry(), self.panel.pos())
        if placement is not None:
            self._placing_panel = True
            self.panel.move(placement.topLeft())
            self._placing_panel = False
        else:
            self._placing_panel = True
            area = screen.availableGeometry()
            reserve = min(self.panel.height() + 4, max(0, area.height() - 120))
            self.scan.setGeometry(bounded_rect(self.scan.geometry(), area.adjusted(0, reserve, 0, 0)))
            placement = panel_placement(self.scan.geometry(), self.panel.size(), area, self.panel.pos())
            if placement is not None:
                self.panel.move(placement.topLeft())
            self._placing_panel = False

    def _update_region(self):
        if self._quitting:
            return False
        try:
            region = native_client_region(self.scan, self.scan.INSET)
        except (OSError, ValueError, RuntimeError) as error:
            self._region = {}
            self._frame = QImage()
            self._metadata = {}
            self.preview.clear_frame()
            self.panel.save_button.setEnabled(False)
            if hasattr(self, "_players"):
                self._invalidate_analysis()
                self.manual_overrides.clear()
                self.isolation_confirmed = False
                self._players = []
                self._last_result = None
                self.scan.set_annotations([])
                if not self.test_enabled:
                    self.panel.set_summary("选区无效", True)
            self._on_capture_error(str(error))
            return False
        changed = region != self._region
        self._region = region
        self.panel.set_capture_info(region, self.scan.screen().name())
        if changed:
            if not getattr(self,'_auto_applying',False):
                self.grid_ready=False
                self.panel.set_grid_ready(False)
            # Reject queued images from the previous region rather than show stale data.
            self._frame = QImage()
            self._metadata = {}
            self.panel.save_button.setEnabled(False)
            self.preview.clear_frame()
            if hasattr(self, "_players"):
                self._invalidate_analysis(preserve_pending=self._analysis_pending or self._processing_busy)
                self.manual_overrides.clear()
                self.isolation_confirmed = False
                self._players = []
                self._last_result = None
                if not self.test_enabled:
                    self.scan.set_annotations([])
                    self.panel.set_summary("待分析" if self.grid_ready else "请先校准网格")
            if self.running:
                self.region_requested.emit(region)
        return True

    def _exclude_windows(self) -> bool:
        windows = [self.scan, self.scan.markers, self.panel]
        if self.tank_picker.isVisible():
            windows.append(self.tank_picker)
        if self.preview.isVisible():
            windows.append(self.preview)
        if self._dialog is not None and self._dialog.isVisible():
            windows.append(self._dialog)
        outcomes = {window.objectName() or window.windowTitle(): exclude_from_capture(window) for window in windows}
        if outcomes != getattr(self, '_capture_affinity_results', None):
            LOG.info('Capture policy: visible in desktop screenshots/recording; affinity_clear=%s', outcomes)
            self._capture_affinity_results = outcomes
        return True

    def set_remote_mode(self, enabled):
        self._cancel_grid_request()
        self._cancel_tank_selection()
        self._restore_remote_windows()
        was_running = self.running
        self.stop_requested.emit()
        self._set_running(False)
        self._invalidate_analysis()
        self.remote_compatible = True
        set_remote_compatible(self.remote_compatible)
        for window in (self.scan, self.scan.markers, self.panel, self.preview, self.tank_picker, self._dialog):
            if window is not None:
                exclude_from_capture(window)
        self.persist()
        if was_running:
            self.toggle_capture()
        self.panel.set_summary('桌面录屏显示已启用')

    def _hide_remote_windows(self, kind):
        self._restore_remote_windows()
        self._remote_capture_kind = kind
        marks = self.scan.markers
        windows = [marks] if marks.displayed_annotations() or marks.calibration_enabled else []
        self._remote_hidden = [window for window in windows if window.isVisible()]
        for window in self._remote_hidden:
            window.hide()
        self._compositor_flushed = flush_compositor() if self._remote_hidden else True
        def timeout():
            if self._remote_capture_kind == kind:
                self._restore_remote_windows()
                if kind[0] == "grid":
                    self._cancel_grid_request()
                self._analysis_pending = False
                self.scan.set_annotations([])
                self._last_result = None
                self._recognition_previous = None
                self._stable_frames = 0
                self.panel.set_summary("取图超时 · 请重试")
        QTimer.singleShot(5000, timeout)

    def _restore_remote_windows(self):
        windows = getattr(self, "_remote_hidden", [])
        self._remote_hidden = []
        self._remote_capture_kind = None
        if not getattr(self, "_quitting", False) and not getattr(self, "_hidden", False):
            was_applying = getattr(self, "_auto_applying", False)
            self._auto_applying = True
            try:
                for window in windows:
                    window.show()
            finally:
                self._auto_applying = was_applying

    def _request_remote_frame(self):
        if (not getattr(self, "remote_compatible", False) or self._quitting or not self.running
                or self._dialog is not None or self.tank_picker.is_selecting or self._grid_busy
                or self._remote_capture_kind is not None):
            return
        if self.preview.isVisible() and self.preview.geometry().intersects(self.scan.geometry()):
            self.scan.set_annotations([])
            self._last_result = None
            self.panel.set_summary('预览覆盖扫描区')
            self.panel.set_status('请将预览移出扫描框，或关闭预览后继续识别。')
            return
        self._remote_capture_id += 1
        request_id = self._remote_capture_id
        self._hide_remote_windows(("frame", request_id))
        request = {**self._region, "remote_capture_id": request_id,
                   'hidden_layers': [window.objectName() for window in self._remote_hidden],
                   'compositor_flushed': self._compositor_flushed}
        def capture():
            if self._remote_capture_kind == ("frame", request_id) and self.running:
                self.snapshot_requested.emit(request)
        QTimer.singleShot(0 if self._compositor_flushed else 35, capture)

    def toggle_capture(self):
        if self.running:
            self._invalidate_analysis()
            self.scan.set_annotations([])
            self._last_result = None
            self._set_running(False)
            self.stop_requested.emit()
            if getattr(self, "remote_compatible", False):
                self._restore_remote_windows()
            self.panel.set_status("采集已暂停，保留最近一帧。")
            return
        if not self._update_region():
            return
        if not self._exclude_windows():
            self.panel.set_status("系统未能排除工具自身的覆盖层，暂无法采集。需要 Windows 10 2004 或更新版本。", True)
            return
        if not self.locked:
            self.set_locked(True)
        self._set_running(True)
        self.panel.set_status("正在采集框内画面；打开预览可检查选区。")
        if getattr(self, "remote_compatible", False):
            self._request_remote_frame()
        else:
            self.start_requested.emit(dict(self._region))

    def _set_running(self, running: bool):
        self.running = running
        self.scan.set_running(running)
        self.panel.setProperty("onDemandCapture", getattr(self, "remote_compatible", False))
        self.panel.set_capture_state(running)
        self.preview.set_capture_state(running)
        self.capture_action.setText("暂停采集" if running else "开始采集")

    @Slot(QImage, dict)
    def _on_frame(self, image: QImage, metadata: dict):
        if getattr(self, "remote_compatible", False) and "remote_capture_id" not in metadata:
            return
        if "remote_capture_id" in metadata:
            if getattr(self, "_remote_capture_kind", None) != ("frame", metadata["remote_capture_id"]):
                return
            self._restore_remote_windows()
        if not self.running or metadata.get("region") != self._region:
            if getattr(self, "remote_compatible", False) and self._analysis_pending and self.running:
                self._request_remote_frame()
            return
        self._frame = image
        self._metadata = dict(metadata)
        self.panel.set_frame_info(metadata)
        if self._dialog is not None and hasattr(self._dialog, "update_frame"):
            self._dialog.update_frame(image, metadata)
        if self._analysis_pending and not self.test_enabled and not self._processing_busy:
            self._analysis_pending = False
            self._submit_analysis(image, metadata)
        if self.preview.isVisible():
            self.preview.update_frame(image, metadata)
        if getattr(self, "_remote_open_preferences", False):
            self._remote_open_preferences = False
            QTimer.singleShot(0, self.open_preferences)

    @Slot(str)
    def _on_capture_error(self, message: str):
        LOG.warning("Capture: %s", message)
        if getattr(self, "remote_compatible", False):
            self._restore_remote_windows()
            self._remote_open_preferences = False
        self._cancel_tank_selection()
        if hasattr(self, "_players"):
            self._invalidate_analysis()
            self.manual_overrides.clear()
            self.isolation_confirmed = False
            self._last_result = None
            self._players = []
            if not self.test_enabled:
                self.scan.set_annotations([])
        if self.running:
            self._set_running(False)
            self.stop_requested.emit()
        self.panel.set_status(message, True)
        self.panel.set_summary("采集异常", True)

    def toggle_lock(self):
        if self._hidden or not self.panel.isVisible():
            self.restore_controls()
            return
        self.set_locked(not self.locked)

    def set_locked(self, locked: bool):
        locked=bool(locked) or getattr(self,'_continuous_analysis',False)
        self.locked = locked
        self.scan.set_locked(locked)
        self.panel.set_locked(locked)
        through_panel = locked and self.panel_passthrough and not getattr(self,'_continuous_analysis',False)
        if through_panel and not (self.hotkeys.registered[HotkeyFilter.LOCK_ID] or self.tray.isVisible()):
            through_panel = False
            self.panel.set_status("恢复快捷键不可用且没有托盘，已保留面板点击。", True)
        self.panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, through_panel)
        set_click_through(self.panel, through_panel)
        self.lock_action.setText("解锁扫描框" if locked else "锁定扫描框")
        self.lock_action.setChecked(locked)
        self._refresh_summary()

    def toggle_attach(self):
        self.attached = not self.attached
        self.panel.set_attached(self.attached)
        self._dock_panel()
        self._avoid_panel_overlap()
        self._save_timer.start()

    def set_pinned(self, enabled: bool):
        self.pinned = bool(enabled)
        self.panel.set_pinned(self.pinned)
        set_topmost(self.panel, self.pinned)
        self.scan.set_topmost(self.pinned)
        if self.preview.isVisible():
            set_topmost(self.preview, self.pinned)
        if self.pinned:
            self._topmost_timer.start()
            self._refresh_topmost()
        else:
            self._topmost_timer.stop()
        if hasattr(self, "_save_timer"):
            self._save_timer.start()

    def _refresh_topmost(self):
        if not self.pinned or self._hidden or self._quitting:
            return
        # Other topmost applications can move above us after the initial pin.
        # Restore back-to-front, keeping our dialogs and menus usable.
        windows = (
            self.scan, self.scan.markers, self.panel, self.preview,
            self.tank_picker, self._dialog,
            self.app.activeModalWidget(), self.app.activePopupWidget(),
        )
        for window in windows:
            if window is not None and window.isVisible() and not window.isMinimized():
                set_topmost(window, True)

    def _cancel_grid_request(self):
        if not getattr(self, "_grid_busy", False):
            return
        if getattr(self, "remote_compatible", False):
            self._restore_remote_windows()
        self._grid_generation += 1
        self._grid_busy = False
        self._pick_after_grid = False
        self._analyze_after_grid = None
        self.panel.set_grid_busy(False)
        self.panel.set_summary("定位已取消")

    def _cancel_tank_selection(self):
        picker = getattr(self, "tank_picker", None)
        if picker is not None and picker.is_selecting:
            picker.cancel_selection()

    def _refresh_tank_button(self):
        key = self.tank_key
        text = f"T：{key[1:-2]} 队 · {key[-1]}（点击重选）" if key else "T 可选：点击格子指定；未选也可参考分析"
        self.panel.tank_button.setToolTip(text)

    def select_tank(self):
        if self._grid_busy or self._dialog is not None:
            return
        if getattr(self, "remote_compatible", False):
            self._restore_remote_windows()
        if self.tank_picker.is_selecting:
            self.tank_picker.cancel_selection()
            return
        if not self.grid_ready:
            self.locate_grid(pick_tank=True)
            return
        if self.test_enabled:
            self.set_test_enabled(False)
        self._invalidate_analysis()
        self._last_result = None
        self.scan.set_annotations([])
        self.scan.set_guides_visible(False)
        self.tank_picker.set_grid(self.team_layout, self.scan.geometry())
        self.tank_picker.set_current_tank(self.tank_key)
        if self.tank_picker.start_selection():
            self.panel.set_tank_selecting(True)
            self.panel.set_summary("点选 T · 右键取消")
        else:
            self.panel.set_summary("无法打开 T 选择")

    @Slot(str)
    def _tank_selected(self, key):
        if self._grid_busy or not self.grid_ready:
            return
        if key not in {cell.key for cell in self.team_layout.cells(self.scan.markers.content_rect())}:
            return
        self._cancel_tank_selection()
        self.tank_key = key
        self._invalidate_analysis()
        self.manual_overrides.clear()
        self.isolation_confirmed = False
        self._players = []
        self._last_result = None
        self.scan.set_annotations([])
        self.panel.set_tank_selecting(False)
        self._refresh_tank_button()
        self.panel.set_summary(f"T · {key[1:-2]} 队 {key[-1]}")
        self.persist()

    def _tank_selection_cancelled(self):
        self.panel.set_tank_selecting(False)
        self._refresh_summary()

    def clear_tank(self):
        self._cancel_tank_selection()
        self._pick_after_grid = False
        self.tank_key = None
        self._invalidate_analysis()
        self._last_result = None
        self._players = []
        self.scan.set_annotations([])
        self._refresh_tank_button()
        self._refresh_summary()
        self.persist()

    def _end_grid_feedback(self):
        if self._dialog is None:
            self.scan.set_guides_visible(False)

    def locate_grid(self, *, pick_tank=False, analyze_after=None):
        if getattr(self,'_continuous_analysis',False):
            self.panel.set_status('请先停止识别')
            return
        if self._grid_busy or self._dialog is not None:
            return
        if self.preview.isVisible() and self.preview.geometry().intersects(self.scan.geometry()):
            self.panel.set_status('请先将预览移出扫描框，或关闭预览后校准。', True)
            return
        self._cancel_tank_selection()
        if self.test_enabled:
            self.set_test_enabled(False)
        self._invalidate_analysis()
        self.manual_overrides.clear()
        self.isolation_confirmed = False
        self._players = []
        self._last_result = None
        self.scan.set_annotations([])
        self.grid_ready = False
        self.panel.set_grid_ready(False)
        self.tank_key = None
        self._refresh_tank_button()
        # Calibration and recognition share exactly the same physical ROI.
        # The frame is user-owned: neither success nor failure may move it.
        screen = self.scan.screen()
        try:
            if not self._exclude_windows():
                raise RuntimeError("系统未能排除工具自身的覆盖层。")
            capture_region = native_client_region(self.scan,self.scan.INSET)
        except (OSError, ValueError, RuntimeError) as error:
            self.panel.set_summary("无法定位格子")
            self.panel.set_status(str(error))
            self.persist()
            return
        self._grid_generation += 1
        self._grid_busy = True
        self._pick_after_grid = bool(pick_tank)
        self._analyze_after_grid = analyze_after if analyze_after in ("all", "fan", "circle") else None
        self.panel.set_grid_busy(True)
        self.panel.set_summary("定位格子中")
        config = {
            "generation": self._grid_generation, "capture_region": capture_region,
            "scan_geometry":_rect_list(self.scan.geometry()),"search_scope":"selection",
            "screen_name": screen.name(), "screen_geometry": _rect_list(screen.geometry()),
            "dpr": screen.devicePixelRatio(),
        }
        self._record_grid('locating',config)
        if getattr(self, "remote_compatible", False):
            token = ("grid", self._grid_generation)
            self._hide_remote_windows(token)
            config['hidden_layers'] = [window.objectName() for window in self._remote_hidden]
            QTimer.singleShot(0 if self._compositor_flushed else 50, lambda: self.grid_requested.emit(config)
                              if self._remote_capture_kind == token and not self._quitting else None)
        else:
            self.grid_requested.emit(config)

    @Slot(object, QImage, dict)
    def _grid_completed(self, detection, frame, metadata):
        if getattr(self, "_remote_capture_kind", None) == ("grid", metadata.get("generation")):
            self._restore_remote_windows()
        if self._quitting or metadata.get("generation") != self._grid_generation or not self._grid_busy:
            return
        old_layout = self.team_layout
        try:
            screen = next((s for s in self.app.screens() if s.name() == metadata["screen_name"]
                           and _rect_list(s.geometry())==metadata['screen_geometry']
                           and abs(s.devicePixelRatio()-metadata['dpr'])<=.001),None)
            if screen is None:
                raise ValueError("显示器已变化，请重新定位。")
            if _rect_list(self.scan.geometry())!=metadata['scan_geometry']:
                raise ValueError('框选范围已变化，请重新校准。')
            source = metadata["capture_region"]
            if frame.width() != source["width"] or frame.height() != source["height"]:
                raise ValueError("定位截图尺寸不一致。")
            if len(detection.layout.groups) != 5:
                raise ValueError("未找到完整的 25 个团队格子。")
            self._auto_applying = True
            if not self._update_region():
                raise ValueError("识别区域超出显示器。")
            if self._region!=source:
                raise ValueError('框选范围已变化，请重新校准。')
            layout = detection.layout
            self.team_layout = layout
            self.scan.set_team_layout(layout)
            self.grid_ready = True
            self._calibrated_geometry=QRect(self.scan.geometry())
            self.panel.set_grid_ready(True)
            self._frame = frame.copy()
            self._metadata = {**self._region, "region": dict(self._region), "elapsed_ms": 0,
                              "captured_at": metadata.get("captured_at"), "frame_number": 0}
            self.panel.set_frame_info(self._metadata)
            if self.preview.isVisible():
                self.preview.update_frame(self._frame, self._metadata)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            self.team_layout = old_layout
            self.scan.set_team_layout(old_layout)
            self._grid_failed(str(error), metadata)
            return
        finally:
            self._auto_applying = False
        self._grid_busy = False
        self.panel.set_grid_busy(False)
        self._record_grid("located", metadata, confidence=detection.confidence,
                          bounds=[detection.bounds.x(), detection.bounds.y(), detection.bounds.width(), detection.bounds.height()])
        self.panel.set_summary("已定位 25 格 · 可分析")
        self.panel.set_status("已在框选范围内校准25格；边框位置和大小保持不变。")
        self.scan.set_guides_visible(True)
        self._grid_feedback_timer.start()
        self.persist()
        if self._pick_after_grid:
            self._pick_after_grid = False
            self.select_tank()
        elif getattr(self, "_analyze_after_grid", None):
            mode = self._analyze_after_grid
            self._analyze_after_grid = None
            self.analyze_mode(mode)

    @Slot(str, dict)
    def _grid_failed(self, message, metadata):
        if getattr(self, "_remote_capture_kind", None) == ("grid", metadata.get("generation")):
            self._restore_remote_windows()
        if self._quitting or metadata.get("generation") != self._grid_generation or not self._grid_busy:
            return
        self._grid_busy = False
        self._pick_after_grid = False
        self._analyze_after_grid = None
        self.grid_ready = False
        self.panel.set_grid_ready(False)
        self.panel.set_grid_busy(False)
        self.panel.set_summary("框内未找到完整25格")
        self.panel.set_status(message)
        self._record_grid("not_found", metadata, error=message)
        self.persist()

    def _record_grid(self, status, metadata, **extra):
        state = {"version": __version__, "status": status, "captured_at": metadata.get("captured_at"),
                 "process_id":os.getpid(),"updated_at":time.time(),"request_generation":metadata.get('generation'),
                 "search_scope":metadata.get('search_scope','selection'),
                 "source_region": metadata.get("capture_region"), "region": dict(self._region),
                 "layout": self.team_layout.to_dict(), "tank_key": self.tank_key, **extra}
        try:
            frame = metadata.get('debug_frame')
            if getattr(self, 'debug_captures', False) and isinstance(frame, QImage) and not frame.isNull():
                frame.save(str(settings_path().parent/'latest-grid-input.png'), 'PNG')
            (settings_path().parent / "latest-grid.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError, TypeError) as error:
            LOG.warning("Could not save grid diagnosis: %s", error)

    def select_analysis_mode(self, mode: str):
        if mode not in ("all", "circle"):
            return
        if self._continuous_analysis or self.test_enabled:
            if mode != self.analysis_mode:
                self.analyze_mode(mode)
        else:
            self.analysis_mode = mode
            self.panel.set_mode(mode)
            self.scan.set_analysis_mode(mode)
            self._save_timer.start()

    def toggle_analysis(self):
        if self._continuous_analysis:
            self.stop_analysis()
        else:
            self.analyze_mode(self.analysis_mode)

    def stop_analysis(self):
        self._invalidate_analysis()
        self.manual_overrides.clear()
        self.isolation_confirmed = False
        if getattr(self, "running", False):
            self._set_running(False)
            self.stop_requested.emit()
            if getattr(self, "remote_compatible", False):
                self._restore_remote_windows()
        self.scan.set_annotations([])
        self._last_result = None
        self.panel.set_analysis_active(False)
        self.panel.set_summary("已停止")

    def analyze_mode(self, mode: str):
        if mode not in ("all", "fan", "circle"):
            return
        if mode == "fan":
            mode = "all"
        if getattr(self, "_grid_busy", False) or (getattr(self, "tank_picker", None)
                                                  and self.tank_picker.is_selecting):
            return
        if not getattr(self, "grid_ready", True) and not self.test_enabled:
            self.analysis_mode = mode
            self.panel.set_mode(mode)
            self.panel.set_summary('请先校准网格')
            self.panel.set_grid_ready(False)
            return
        self.analysis_mode = mode
        self._continuous_analysis = not self.test_enabled
        self._analysis_started_at = time.perf_counter()
        self._first_result_ms = None
        self.panel.set_analysis_active(self._continuous_analysis)
        self.set_locked(self._continuous_analysis)
        self._stable_frames = 0
        self._last_diagnostic_signature = None
        self._generation += 1
        if hasattr(self, '_team_confirmation'):
            self._team_confirmation.reset()
        self._confirmed_team_result = None
        self._recognition_previous = None
        self._analysis_attempts = 0
        self.panel.set_mode(mode)
        self.scan.set_analysis_mode(mode)
        if self.test_enabled:
            self.apply_analysis(demo_result(mode))
        else:
            self._analysis_pending = True
            self._last_result = None
            self.scan.set_annotations([])
            self.panel.set_summary("分析中")
            if not self.running:
                self.toggle_capture()
            elif getattr(self, "remote_compatible", False):
                self._request_remote_frame()
        self._save_timer.start()

    def _continuous_tick(self):
        if (self._analysis_pending and not self._processing_busy and self.remote_compatible
                and self.running and not self._quitting):
            self._request_remote_frame()
            return
        if (not self._continuous_analysis or self._quitting or self.test_enabled
                or not self.running or self._processing_busy or self._analysis_pending
                or self._dialog is not None or self._grid_busy or self.tank_picker.is_selecting):
            return
        self._analysis_pending = True
        if self.remote_compatible:
            self._request_remote_frame()

    def _invalidate_analysis(self, *, preserve_pending=False):
        self._generation += 1
        self._recognition_previous = None
        self._analysis_attempts = 0
        self._analysis_pending = bool(preserve_pending)
        self._stable_frames = 0
        if hasattr(self, '_team_confirmation'):
            self._team_confirmation.reset()
        self._confirmed_team_result = None
        if not preserve_pending and getattr(self, "_continuous_analysis", False):
            self._continuous_analysis = False
            self.panel.set_analysis_active(False)
            self.set_locked(False)

    def set_test_enabled(self, enabled: bool):
        self._cancel_grid_request()
        self._cancel_tank_selection()
        self._invalidate_analysis()
        self.test_enabled = bool(enabled)
        self.panel.set_test_state(self.test_enabled)
        self.scan.set_test_enabled(self.test_enabled)
        if self.test_enabled:
            self.apply_analysis(demo_result(self.analysis_mode))
        else:
            self.scan.set_annotations([])
            self._last_result = None
            self.panel.set_summary("待分析" if not self._frame.isNull() else "待识别")

    def _submit_analysis(self, image, metadata):
        self._processing_busy = True
        self._analysis_frame = image.copy()
        config = {"generation": self._generation, "layout": self.team_layout.to_dict(),
                  "tank_key": self.tank_key, "snapshot_stage": self.snapshot_stage,
                  "isolation_confirmed": self.isolation_confirmed,
                  "mode": self.analysis_mode,
                  'prefer_exact': getattr(self, 'prefer_exact', False),
                  "overrides": {key: dict(value) for key, value in self.manual_overrides.items()}}
        self.analysis_requested.emit(image, dict(metadata), config)

    @Slot(object, object, object)
    def _analysis_completed(self, result, players, metadata):
        self._processing_busy = False
        if self._quitting or self.test_enabled or metadata.get("generation") != self._generation:
            if (not self._quitting and not self.test_enabled and self._analysis_pending
                    and getattr(self, "remote_compatible", False)):
                self._request_remote_frame()
            return
        self._players = list(players)
        self._analysis_metadata = metadata
        self._analysis_attempts += 1
        signature = tuple((p.player_key, p.color if p.alive else 'none', p.stacks if p.alive else 0,
                           p.alive, p.isolated if p.alive else False, p.is_tank) for p in players)
        if getattr(self, '_continuous_analysis', False) and metadata.get('vision_backend') == 'learned':
            self._complete_learned_snapshot(result, players, metadata, signature)
            return
        if getattr(self, "_continuous_analysis", False):
            # Whole snapshots must agree. Do not stitch per-player votes from
            # different moments into a team state that never actually existed.
            signature = (signature, tuple(metadata.get("isolation_unconfirmed_keys", [])))
            valid = result.status in ("solved", "no_solution")
            same = valid and signature == self._recognition_previous
            self._stable_frames = self._stable_frames + 1 if same else (1 if valid else 0)
            self._recognition_previous = signature if valid else None
            self._analysis_pending = False
            if self._stable_frames >= 2:
                if getattr(self, "_first_result_ms", None) is None and getattr(self, "_analysis_started_at", None) is not None:
                    self._first_result_ms = (time.perf_counter() - self._analysis_started_at) * 1000
                    LOG.info("First confirmed result mode=%s elapsed_ms=%.1f", self.analysis_mode, self._first_result_ms)
                self.apply_analysis(result)
                self.panel.set_status(result.details)
                self.panel.set_summary(result.summary, result.status == "no_solution")
                self.isolation_confirmed = False
                self.manual_overrides.clear()
            else:
                self.scan.set_annotations([])
                self._last_result = None
                self.panel.set_summary(f"确认中 {self._stable_frames}/2" if valid else result.summary)
                self.panel.set_status(result.details)
            diagnosis = (signature, result.status, min(self._stable_frames, 2))
            if diagnosis != self._last_diagnostic_signature:
                self._record_analysis(result, players, metadata)
                self._last_diagnostic_signature = diagnosis
            return
        if result.status in ("solved", "no_solution"):
            if signature != self._recognition_previous:
                self._recognition_previous = signature
                if self._analysis_attempts < 4:
                    self._analysis_pending = True
                    self.panel.set_summary("确认中")
                else:
                    self.scan.set_annotations([])
                    self.panel.set_summary("画面变化")
                if self._analysis_pending and getattr(self, "remote_compatible", False):
                    self._request_remote_frame()
                return
        elif result.status == "needs_recognition" and self._analysis_attempts < 3:
            self._analysis_pending = True
        self.apply_analysis(result)
        self.panel.set_status(result.details)
        self.panel.set_summary(result.summary, result.status == "no_solution")
        if not self._analysis_pending:
            self._record_analysis(result, players, metadata)
            self.isolation_confirmed = False
            self.manual_overrides.clear()
        elif getattr(self, "remote_compatible", False):
            self._request_remote_frame()

    def _complete_learned_snapshot(self, result, players, metadata, signature):
        if not hasattr(self, '_team_confirmation'):
            self._team_confirmation = SnapshotConfirmation()
            self._confirmed_team_result = None
        diagnostics = metadata.get('diagnostics', {})
        trustworthy = result.status in ('solved', 'no_solution') and all(
            diagnostics.get(p.player_key, {}).get('localized', False)
            and (not p.alive or (p.color in ('none','red','black') and isinstance(p.isolated, bool)
                 and (p.color=='none' or p.stacks in (1,2,3,4)))) for p in players)
        seconds = metadata.get('captured_at')
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            seconds = time.perf_counter()
        trustworthy = trustworthy and math.isfinite(seconds)
        if trustworthy and self._team_confirmation.confirmed not in (None, signature):
            # Publish this complete frame immediately as a reference. Never
            # retain old actions or mix individual players across moments.
            self._team_confirmation.reset()
            self._confirmed_team_result=None
        status, accepted = self._team_confirmation.update(signature, seconds, trustworthy=trustworthy)
        metadata['confirmation_status'] = status
        metadata['observed_players'] = [dict(key=p.player_key, color=p.color, stacks=p.stacks,
            alive=p.alive, isolated=p.isolated, is_tank=p.is_tank) for p in players]
        self._analysis_pending = False
        self._recognition_previous = signature if trustworthy else None
        self._stable_frames = 2 if accepted is not None else self._team_confirmation.count
        if trustworthy and status == 'confirmed':
            self._confirmed_team_result = deepcopy(result), list(players)
            self.isolation_confirmed = False
            self.manual_overrides.clear()
        if trustworthy:
            published = deepcopy(result)
            published.reference = result.reference or status != 'confirmed'
            self._players = list(players)
            self.apply_analysis(published)
            self.panel.set_status(published.details)
            if getattr(self, '_first_result_ms', None) is None and getattr(self, '_analysis_started_at', None) is not None:
                self._first_result_ms = (time.perf_counter()-self._analysis_started_at)*1000
        else:
            published = result
            self._confirmed_team_result = None
            self.scan.set_annotations([])
            self._last_result = None
            self.panel.set_status(result.details)
            geometry_missing=any(not diagnostics.get(p.player_key,{}).get('localized',False) for p in players)
            self.panel.set_summary('格子待定位' if geometry_missing else result.summary or '画面待确认')
        metadata['published'] = trustworthy
        metadata['reference'] = published.reference if trustworthy else False
        metadata['published_kind'] = ('reference' if published.reference else 'confirmed') if trustworthy else 'invalid'
        metadata['confirmation_hold_ms'] = 0.
        diagnosis = (signature, result.status, status, metadata['reference'], tuple(metadata.get('reference_keys',[])))
        if diagnosis != getattr(self, '_last_diagnostic_signature', None):
            self._record_analysis(published, players, metadata)
            self._last_diagnostic_signature = diagnosis

    def _record_analysis(self, result, players, metadata):
        """Keep the latest local diagnosis without player names or screen images."""
        LOG.info("Analysis mode=%s status=%s unknown=%s difference=%s region=%s ms=%.1f init=%.1f vision=%.1f plan=%.1f",
                 self.analysis_mode, result.status, len(metadata.get("unknown_keys", [])),
                 result.difference, self._region, metadata.get("processing_ms", 0),
                 metadata.get("initialization_ms", 0), metadata.get("recognition_ms", 0), metadata.get("planning_ms", 0))
        state = {
            "version": __version__,
            "remote_compatible": getattr(self, "remote_compatible", False),
            "captured_at": metadata.get("captured_at"), "mode": self.analysis_mode,
            "status": result.status, "summary": result.summary, "region": dict(self._region),
            "current_red": result.current_red, "current_black": result.current_black,
            "current_difference": (result.current_red-result.current_black
                if result.current_red is not None and result.current_black is not None else None),
            "instruction": result.instruction,
            "balanced": result.balanced,
            "continuous": getattr(self, "_continuous_analysis", False),
            "first_result_ms": getattr(self, "_first_result_ms", None),
            "recognition_ms": metadata.get("recognition_ms"),
            "vision_backend": metadata.get("vision_backend", "learned"),
            "initialization_ms": metadata.get("initialization_ms"),
            "planning_ms": metadata.get("planning_ms"),
            "processing_ms": metadata.get("processing_ms"),
            "confirmation_status": metadata.get("confirmation_status"),
            "confirmation_hold_ms": metadata.get("confirmation_hold_ms"),
            "observed_players": metadata.get("observed_players"),
            "stable_frames": getattr(self, "_stable_frames", 0),
            "published": metadata.get('published', (getattr(self, "_stable_frames", 0) >= 2
                          if getattr(self, "_continuous_analysis", False) else True)),
            "reference": result.reference,
            "published_kind": metadata.get('published_kind'),
            "reference_keys": metadata.get('reference_keys', []),
            "reference_reasons": metadata.get('reference_reasons', {}),
            "model_output_policy": metadata.get('model_output_policy'),
            "strategy_policy": ('buff_groups_then_refinement' if result.plan and result.plan.command_style
                                else 'balanced' if result.balanced else 'individual_fallback'),
            'strategy': result.strategy,
            'capture_policy': 'desktop_visible_markers_only_hidden_for_recognition',
            'hidden_layers': metadata.get('hidden_layers', []),
            "guide_keys": list(result.plan.centers) if result.plan else [],
            "guide_convention": '1定点，2单走，3在1旁；仅跟1或1后3' if result.plan and result.plan.command_style else None,
            "tank_key": self.tank_key, "snapshot_stage": self.snapshot_stage,
            "difference": result.difference, "final_red": result.final_red, "final_black": result.final_black,
            "players": [{"key": p.player_key, "color": p.color, "stacks": p.stacks,
                         "alive": p.alive, "isolated": p.isolated, "is_tank": p.is_tank} for p in players],
            "diagnostics": metadata.get("diagnostics", {}),
            "warnings": metadata.get("warnings", []),
            "reference_without_tank": metadata.get("reference_without_tank", False),
            "isolation_unconfirmed_keys": metadata.get("isolation_unconfirmed_keys", []),
            "annotations": [{"key": a.player_key, "kind": a.kind, "order": a.order,
                              "role": getattr(a, "role", "helper")} for a in result.annotations],
        }
        if result.strategy:
            state['strategy_policy'] = result.strategy['policy']
        if result.plan:
            state['actions'] = [dict(key=action.player_key, fan=action.fan, circles=list(action.circles))
                                for action in result.plan.actions]
        unknown = [p for p in players if p.alive and (
            p.color == "unknown" or (p.color in ("red", "black") and p.stacks is None))]
        for player in unknown:
            detail = metadata.get("diagnostics", {}).get(player.player_key, {})
            group, slot = player.player_key[1:].split("p")
            LOG.warning("Buff待识别 %s队%s (%s): %s; type=%.3f label=%.3f stack=%.3f margin=%.3f template=%s rect=%s",
                        group, slot, player.player_key, detail.get("source", "缺少识别证据"),
                        detail.get("color_confidence", 0), detail.get("label_confidence", 0), detail.get("stack_confidence", 0),
                        detail.get("type_margin", 0), detail.get("template"), detail.get("icon_rect"))
        if getattr(self, "debug_captures", False) and not self._analysis_frame.isNull():
            try:
                original = settings_path().parent/'latest-input.png'
                self._analysis_frame.save(str(original), 'PNG')
                state['input_frame_file'] = str(original)
                if hasattr(self, 'scan'):
                    marked = settings_path().parent/'latest-marked.png'
                    annotations = result.annotations if metadata.get('published', True) else []
                    self.scan.markers.annotated_frame(self._analysis_frame, annotations).save(str(marked), 'PNG')
                    state['marked_frame_file'] = str(marked)
                folder = settings_path().parent / "latest-unrecognized"
                folder.mkdir(parents=True, exist_ok=True)
                all_folder = settings_path().parent / "latest-buffs"
                all_folder.mkdir(parents=True, exist_ok=True)
                cells = {cell.key: cell for cell in self.team_layout.cells(self._analysis_frame.size())}
                paths, all_paths = {}, {}
                unknown_keys = {p.player_key for p in unknown}
                for player in players:
                    cell = cells[player.player_key]
                    rect = cell.buff_rect.toAlignedRect().intersected(self._analysis_frame.rect())
                    band = metadata.get('diagnostics', {}).get(player.player_key, {}).get('buff_band')
                    if band and len(band) == 4:
                        x1,y1,x2,y2=map(int,band)
                        rect=QRect(x1,y1,x2-x1,y2-y1).intersected(self._analysis_frame.rect())
                    crop = self._analysis_frame.copy(rect)
                    target = all_folder / (player.player_key + ".png")
                    if not rect.isEmpty() and crop.save(str(target), "PNG"):
                        all_paths[player.player_key] = str(target)
                    if player.player_key in unknown_keys:
                        target = folder / (player.player_key + ".png")
                        if not rect.isEmpty() and crop.save(str(target), "PNG"):
                            paths[player.player_key] = str(target)
                state["debug_crop_files"] = paths
                state["buff_crop_files"] = all_paths
            except (OSError, ValueError, KeyError) as error:
                LOG.warning("Failed to save debug Buff crops: %s", error)
        try:
            (settings_path().parent / "latest-analysis.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            logging.getLogger('lisha.analysis_history').info(json.dumps(state, ensure_ascii=False, default=str))
        except (OSError, TypeError, ValueError) as error:
            LOG.warning("Could not save local analysis diagnosis: %s", error)

    @Slot(str, dict)
    def _analysis_failed(self, message, metadata):
        self._processing_busy = False
        if self._quitting or metadata.get("generation") != self._generation or self.test_enabled:
            if (not self._quitting and not self.test_enabled and self._analysis_pending
                    and getattr(self, "remote_compatible", False)):
                self._request_remote_frame()
            return
        LOG.error("Analysis: %s", message)
        try:
            (settings_path().parent/'latest-failure.json').write_text(json.dumps(
                dict(error=message, metadata=metadata, recorded_at=time.time()), ensure_ascii=False, default=str, indent=2), encoding='utf8')
            if getattr(self, 'debug_captures', False) and not self._analysis_frame.isNull():
                self._analysis_frame.save(str(settings_path().parent/'latest-failed-input.png'), 'PNG')
        except OSError:
            LOG.exception('Could not write failure diagnostics')
        self._recognition_previous = None
        self._stable_frames = 0
        if hasattr(self, '_team_confirmation'):
            self._team_confirmation.reset()
        self._confirmed_team_result = None
        self._last_result = None
        self.scan.set_annotations([])
        self.panel.set_status(message, True)
        self.panel.set_summary("识别异常", True)

    def apply_analysis(self, result: AnalysisResult):
        if result.status == "demo" and not self.test_enabled:
            return
        if self.test_enabled and result.status != "demo":
            return
        self._last_result = result
        if hasattr(self, '_analysis_frame'):
            self._published_frame = self._analysis_frame.copy()
        self.scan.set_annotations(result.annotations)
        self.panel.set_result(result)

    def set_player_states(self, players):
        """Recognition may publish validated grid identities through this boundary."""
        self._players = list(players)
        if not self.test_enabled:
            self.apply_analysis(evaluate(self._players, self.analysis_mode, snapshot_stage=self.snapshot_stage))

    def _refresh_summary(self):
        self.panel.set_grid_ready(getattr(self,'grid_ready',False))
        if getattr(self, "_grid_busy", False):
            self.panel.set_summary("定位格子中")
        elif getattr(self, "tank_picker", None) and self.tank_picker.is_selecting:
            self.panel.set_summary("点选 T · 右键取消")
        elif self._last_result is not None:
            self.panel.set_result(self._last_result)
        else:
            if self.test_enabled:
                text = "测试"
            elif not getattr(self, "grid_ready", True):
                text = "请先校准网格"
            else:
                text = "待分析"
            self.panel.set_summary(text)

    def _open_context_menu(self, point):
        menu = QMenu(self.panel)
        menu.addAction("暂停采集" if self.running else "开始采集", self.toggle_capture)
        menu.addAction("解锁边框" if self.locked else "锁定边框", self.toggle_lock)
        menu.addSeparator()
        menu.addAction("预览", self.open_preview)
        menu.addAction("保存截图", self.save_frame).setEnabled(not self._frame.isNull())
        menu.addAction("外观 / 尺寸", self.open_preferences)
        exact = menu.addAction('正平优先')
        exact.setCheckable(True)
        exact.setChecked(self.prefer_exact)
        exact.triggered.connect(self.set_exact_preference)
        menu.addAction('导出调试包', self.export_debug_bundle)
        menu.addAction("重置位置", self.reset_layout)
        menu.exec(self.panel.mapToGlobal(point))
        menu.deleteLater()

    def open_preview(self):
        if getattr(self, "remote_compatible", False):
            self._restore_remote_windows()
        if not self.preview.isVisible():
            screen = self.panel.screen()
            area = screen.availableGeometry()
            self.preview.resize(min(self.preview.width(), max(400, area.width() - 40)),
                                min(self.preview.height(), max(300, area.height() - 60)))
            rect = QRect(area.center() - self.preview.rect().center(), self.preview.size())
            self.preview.move(bounded_rect(rect, area, (400, 300)).topLeft())
        self.preview.show()
        exclude_from_capture(self.preview)
        self.preview.raise_()
        self.preview.activateWindow()
        self.preview.set_capture_state(self.running)
        if not self._frame.isNull():
            self.preview.update_frame(self._frame, self._metadata)
        if getattr(self, "remote_compatible", False) and self.running:
            self._request_remote_frame()

    def save_frame(self):
        if getattr(self, "remote_compatible", False):
            self._restore_remote_windows()
        if self._frame.isNull():
            self.panel.set_status("先开始采集，收到画面后即可保存。")
            return
        source = (self._published_frame if self._last_result is not None and not self._published_frame.isNull()
                  else self._frame)
        annotations = self._last_result.annotations if self._last_result is not None else []
        frame = self.scan.markers.annotated_frame(source, annotations)
        name = f"lisha-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.png"
        folder = Path(self.settings.get("capture_directory", str(Path.home() / "Pictures" / "戾煞配平")))
        dialog = QFileDialog(self.panel, "保存截图（含标记）", str(folder / name), "PNG 图像 (*.png)")
        # A Qt dialog can be excluded from capture; a native common dialog may not be.
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("png")
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        dialog.show()
        exclude_from_capture(dialog)
        previous_dialog = self._dialog
        self._dialog = dialog
        if dialog.exec() == QDialog.DialogCode.Accepted:
            target = Path(dialog.selectedFiles()[0])
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not frame.save(str(target), "PNG"):
                    raise OSError("无法写入 PNG 文件。")
            except OSError as error:
                self.panel.set_status(f"保存失败：{error}", True)
            else:
                self.settings["capture_directory"] = str(target.parent)
                self.persist()
                self.panel.set_status(f"已保存截图：{target.name}")
        dialog.deleteLater()
        self._dialog = previous_dialog
        if getattr(self, "remote_compatible", False) and self._analysis_pending:
            self._request_remote_frame()

    def open_preferences(self):
        if getattr(self, "_grid_busy", False):
            return
        if getattr(self, "remote_compatible", False):
            self._restore_remote_windows()
        self._cancel_tank_selection()
        if self._dialog is not None:
            self._dialog.raise_()
            return
        self._invalidate_analysis()
        if getattr(self, "remote_compatible", False) and self._frame.isNull():
            self._remote_open_preferences = True
            if not self.running:
                self.toggle_capture()
            else:
                self._request_remote_frame()
            return
        if not self.running and not getattr(self, "remote_compatible", False):
            self.toggle_capture()
        source_region = dict(self._region)
        dialog = LayoutPreferencesDialog(self.app.screens(), self.scan, self.panel_opacity,
                                          self.border_opacity, self.panel_passthrough, self.panel,
                                          team_layout=self.team_layout, frame=self._frame,
                                          tank_key=self.tank_key, snapshot_stage=self.snapshot_stage,
                                          overrides=self.manual_overrides,
                                          isolation_confirmed=self.isolation_confirmed)
        dialog.recovery_hint.setText(
            self.hotkeys.labels[HotkeyFilter.LOCK_ID] + " 恢复操作")
        self._dialog = dialog
        dialog.remote_box.setChecked(getattr(self, "remote_compatible", False))
        dialog.layout_changed.connect(self._preview_layout)
        self.scan.set_guides_visible(True)
        self.scan.set_annotations([])
        dialog.reset_requested.connect(self.reset_layout)
        dialog.show()
        exclude_from_capture(dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = dialog.screen_box.currentData()
            screen = next((s for s in self.app.screens() if s.name() == selected), self.scan.screen())
            rect = QRect(self.scan.geometry())
            rect.setSize(QSize(dialog.width_box.value(), dialog.height_box.value()))
            rect.moveTopLeft(QPoint(dialog.x_box.value(), dialog.y_box.value()))
            self.scan.setGeometry(bounded_rect(rect, screen.availableGeometry()))
            self.panel_opacity = dialog.panel_slider.value() / 100
            self.border_opacity = dialog.border_slider.value() / 100
            self.panel_passthrough = dialog.pass_box.isChecked()
            remote_changed = dialog.remote_box.isChecked() != getattr(self, "remote_compatible", False)
            if remote_changed:
                self.set_remote_mode(dialog.remote_box.isChecked())
            self.team_layout = dialog.selected_layout
            self.tank_key = None
            self.snapshot_stage = 'after_tank'
            self.panel.setWindowOpacity(self.panel_opacity)
            self.scan.set_border_opacity(self.border_opacity)
            self.set_locked(self.locked)
            self.set_pinned(self.pinned)
            self._dock_panel()
            region_valid = self._update_region()
            self.grid_ready = bool(region_valid)
            self._calibrated_geometry=QRect(self.scan.geometry()) if self.grid_ready else None
            self.panel.set_grid_ready(self.grid_ready)
            if region_valid and self._region and self._region == source_region:
                self.manual_overrides = {key: dict(value) for key, value in dialog.overrides.items()}
                self.isolation_confirmed = dialog.isolation_box.isChecked()
            else:
                self.manual_overrides.clear()
                self.isolation_confirmed = False
            self._players = []
            self._last_result = None
            self._invalidate_analysis()
            self.scan.set_annotations([])
            self.persist()
            self._refresh_tank_button()
        self.scan.set_team_layout(self.team_layout)
        self.scan.set_guides_visible(False)
        if self.test_enabled:
            self.apply_analysis(demo_result(self.analysis_mode))
        else:
            if self._last_result is not None:
                self.scan.set_annotations(self._last_result.annotations)
            self._refresh_summary()
        self._dialog = None
        dialog.deleteLater()

    def _preview_layout(self, layout):
        self.scan.set_team_layout(layout)

    def reset_layout(self):
        area = self.app.primaryScreen().availableGeometry()
        self.attached = True
        self.panel.set_attached(True)
        self.scan.setGeometry(bounded_rect(QRect(area.left() + 80, area.top() + 110, 360, 480), area))
        self.set_locked(False)
        self.set_pinned(self.pinned)
        self._dock_panel()
        self._update_region()
        self.persist()

    def toggle_visibility(self):
        if getattr(self, "remote_compatible", False):
            self._restore_remote_windows()
        self._cancel_grid_request()
        self._cancel_tank_selection()
        if self._hidden or not self.panel.isVisible():
            self._hidden = False
            self.scan.show()
            self.panel.show()
            self._exclude_windows()
            self.set_locked(self.locked)
            self.set_pinned(self.pinned)
        else:
            self._hidden = True
            self.scan.hide()
            self.panel.hide()
            self.preview.hide()

    def restore_controls(self):
        if getattr(self, "remote_compatible", False):
            self._restore_remote_windows()
            self._invalidate_analysis()
        self._cancel_tank_selection()
        self._hidden = False
        self.scan.show()
        self.panel.show()
        self.set_locked(False)
        self.set_pinned(self.pinned)
        self._exclude_windows()
        self.panel.raise_()
        self.panel.activateWindow()

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self.app.windowIcon(), self)
        self.tray.setToolTip("戾煞 · 配平助手")
        self.tray_menu = QMenu()
        restore = self.tray_menu.addAction("显示工具并恢复操作")
        restore.triggered.connect(self.restore_controls)
        self.capture_action = self.tray_menu.addAction("开始采集")
        self.capture_action.triggered.connect(self.toggle_capture)
        self.lock_action = self.tray_menu.addAction("锁定扫描框")
        self.lock_action.setCheckable(True)
        self.lock_action.triggered.connect(self.toggle_lock)
        self.tray_menu.addAction("打开预览").triggered.connect(self.open_preview)
        self.tray_menu.addAction("外观与位置").triggered.connect(self.open_preferences)
        self.tray_menu.addAction('导出调试包').triggered.connect(self.export_debug_bundle)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("退出").triggered.connect(self.quit)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        elif not self.hotkeys.registered[HotkeyFilter.HIDE_ID]:
            self.panel.set_hotkey_status("托盘与恢复快捷键均不可用；请保留面板显示。")

    def set_exact_preference(self, enabled):
        self.prefer_exact = bool(enabled)
        self.persist()
        if self._continuous_analysis:
            self.analyze_mode(self.analysis_mode)
        elif self._players:
            from .live_strategy import evaluate_exact
            evaluator = evaluate_exact if self.prefer_exact else evaluate
            self.apply_analysis(evaluator(self._players, self.analysis_mode))

    def export_debug_bundle(self):
        folder = settings_path().parent
        try:
            record_environment(folder)
            desktop = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or str(folder))
            target = desktop/f'戾煞调试包-{datetime.now():%Y%m%d-%H%M%S-%f}.zip'
            export_bundle(folder, target)
        except (OSError, ValueError) as error:
            LOG.exception('Debug export failed')
            self.panel.set_status(f'调试包导出失败：{error}', True)
            return None
        LOG.info('Debug bundle exported to %s', target)
        self.panel.set_status(f'调试包已保存到桌面：{target.name}')
        self.tray.showMessage('调试包已导出', str(target), QSystemTrayIcon.MessageIcon.Information, 8000)
        return target

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_controls()

    def _screen_removed(self, screen):
        self._cancel_grid_request()
        self._cancel_tank_selection()
        self.grid_ready = False
        if self.running:
            self._set_running(False)
            self.stop_requested.emit()
        QTimer.singleShot(150, self.reset_layout)

    def persist(self):
        self.settings.update({
            "scan_geometry": _rect_list(self.scan.geometry()),
            "panel_geometry": _rect_list(self.panel.geometry()),
            "attached": self.attached,
            "panel_passthrough": self.panel_passthrough,
            "panel_opacity": self.panel_opacity,
            "border_opacity": self.border_opacity,
            "pinned": self.pinned,
            "analysis_mode": self.analysis_mode,
            "team_layout": self.team_layout.to_dict(),
            "grid_ready": self.grid_ready,
            "tank_key": self.tank_key,
            "snapshot_stage": self.snapshot_stage,
            "remote_compatible": getattr(self, "remote_compatible", False),
            'prefer_exact': getattr(self, 'prefer_exact', False),
        })
        try:
            save_settings(self.settings)
        except (OSError, ValueError, TypeError) as error:
            LOG.warning("Settings: %s", error)
            self.panel.set_status(f"位置设置暂未保存：{error}", True)

    def quit(self):
        self.app.quit()

    def shutdown(self):
        if self._quitting:
            return
        self._quitting = True
        self._continuous_analysis = False
        self._analysis_pending = False
        self._continuous_timer.stop()
        self._topmost_timer.stop()
        self.analysis_worker.completed.disconnect(self._analysis_completed)
        self.analysis_worker.failed.disconnect(self._analysis_failed)
        self._save_timer.stop()
        self._region_timer.stop()
        self._grid_feedback_timer.stop()
        self._cancel_grid_request()
        self._cancel_tank_selection()
        self.persist()
        self.hotkeys.close()
        self.app.removeNativeEventFilter(self.hotkeys)
        # Release MSS's thread-local DC in its owning thread before the loop exits.
        if self.thread.isRunning():
            QMetaObject.invokeMethod(self.worker, "stop_capture", Qt.ConnectionType.BlockingQueuedConnection)
            self.thread.quit()
            self.thread.wait()
        if self.analysis_thread.isRunning():
            self.analysis_thread.requestInterruption()
            self.analysis_thread.quit()
            self.analysis_thread.wait()
        self.tray.hide()
        self.preview.hide()
        self.scan.hide()
        self.panel.hide()
        # These are independent top-level windows, not QObject children of
        # this controller. Destroy them on the GUI thread now; leaving them
        # for Python GC can destroy QWidget cycles in a later worker thread.
        for window in (self.tank_picker, self.preview, self.scan.markers, self.scan, self.panel):
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


def _configure_logging():
    global _crash_log_handle
    folder = settings_path().parent
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    _crash_log_handle = (folder/'crash.log').open('a', encoding='utf8')
    faulthandler.enable(file=_crash_log_handle, all_threads=True)
    history = logging.getLogger('lisha.analysis_history')
    history.propagate = False
    history.setLevel(logging.INFO)
    handler = RotatingFileHandler(folder/'analysis-history.jsonl', maxBytes=2_000_000, backupCount=1, encoding='utf8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    history.addHandler(handler)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("戾煞配平")
    app.setOrganizationName("LishaBalancer")
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setWindowIcon(_icon())
    _configure_logging()
    try:
        record_environment(settings_path().parent)
    except Exception:
        LOG.exception('Environment report unavailable')
    if '--export-debug' in sys.argv:
        index = sys.argv.index('--export-debug')
        explicit = index+1 < len(sys.argv) and not sys.argv[index+1].startswith('--')
        desktop = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or str(settings_path().parent))
        target = Path(sys.argv[index+1]) if explicit else desktop/f'戾煞调试包-{datetime.now():%Y%m%d-%H%M%S-%f}.zip'
        export_bundle(settings_path().parent, target)
        if not explicit:
            QMessageBox.information(None, '调试包已导出', f'请把这个文件发给团长：\n{target}')
        return 0
    if "--check-runtime" in sys.argv:
        from .vision import VisionEngine
        from .grid_detection import detect_team_grid
        image = QImage(346, 505, QImage.Format.Format_RGB32)
        image.fill(QColor("#14232e"))
        engine = VisionEngine(TeamLayout())
        assert len(engine.analyze(image)) == 25
        assert detect_team_grid(image) is None
        from .live_strategy import evaluate_current
        from .analysis import PlayerState
        values = [-1,-1,1,-1,1,1,-1,-1,-1,1,1,1,1,1,1,1,1,-1,-1,-1,-1,-1,-1,-1,-1]
        checked = evaluate_current([PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if value>0 else 'black', abs(value))
                                    for i, value in enumerate(values)])
        assert checked.strategy['quota']['selected_count'] == 1 and checked.final_black == 13
        record_environment(settings_path().parent)
        LOG.info("Runtime check passed: Qt, capture module, OpenCV, %s recognizer/assets, grid and analysis",engine.backend)
        return 0
    # A second launch restores the existing window instead of competing for hotkeys.
    key = hashlib.sha256(str(Path.home()).encode("utf-8")).hexdigest()[:12]
    server_name = "LishaOverlay-" + key
    connection = QLocalSocket()
    requested_command = _requested_command(sys.argv)
    connection.connectToServer(server_name)
    if connection.waitForConnected(180):
        connection.write(requested_command.encode("ascii"))
        connection.waitForBytesWritten(300)
        connection.disconnectFromServer()
        return 0
    if requested_command == "quit":
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer(app)
    if not server.listen(server_name):
        QMessageBox.warning(None, "无法启动", "另一个工具实例正在启动，请稍后重试。")
        return 1
    startup_trace = None
    LOG.info("Startup stage: constructing controller")
    try:
        try:
            startup_trace = (settings_path().parent / "startup-hang.log").open("w", encoding="utf-8")
            faulthandler.dump_traceback_later(8, repeat=False, file=startup_trace)
        except (OSError, RuntimeError, ValueError) as error:
            LOG.warning("Startup watchdog unavailable: %s", error)
        controller = OverlayController(app)
        LOG.info("Startup stage: controller ready")
    finally:
        if startup_trace is not None:
            faulthandler.cancel_dump_traceback_later()
            startup_trace.close()

    def new_connection():
        commands = []
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if not socket.bytesAvailable():
                socket.waitForReadyRead(150)
            commands.append(bytes(socket.readAll()).decode("ascii", errors="ignore").strip())
            socket.close()
            socket.deleteLater()
        if "quit" in commands:
            controller.quit()
        else:
            for command in commands:
                dispatch(command)

    def dispatch(command):
        if command == "locate":
            controller.locate_grid()
        elif command.startswith("tank:"):
            controller._tank_selected(command.split(":", 1)[1])
        elif command in ("analyze:all", "analyze:fan", "analyze:circle"):
            controller.analyze_mode(command.split(":", 1)[1])
        else:
            controller.restore_controls()

    server.newConnection.connect(new_connection)
    if requested_command != "restore":
        QTimer.singleShot(200, lambda: dispatch(requested_command))

    def report_exception(kind, error, traceback):
        LOG.error("Unhandled exception", exc_info=(kind, error, traceback))
        controller.panel.set_status(f"出现异常：{error}；详情已写入本地日志。", True)

    sys.excepthook = report_exception
    LOG.info("Starting overlay %s", __version__)
    try:
        return app.exec()
    finally:
        controller.shutdown()
        server.close()
