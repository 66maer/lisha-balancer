"""Region-only capture whose resources live entirely in its Qt worker thread."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage


def normalize_region(region: dict[str, Any]) -> dict[str, int]:
    """Validate an integer physical-pixel rectangle, including negative origins."""
    if not isinstance(region, dict):
        raise ValueError("截图区域必须是包含 left、top、width、height 的对象。")
    result: dict[str, int] = {}
    for key in ("left", "top", "width", "height"):
        value = region.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"截图区域 {key} 必须是物理像素整数。")
        result[key] = value
    if result["width"] <= 0 or result["height"] <= 0:
        raise ValueError("截图区域的宽度和高度必须大于零。")
    return result


def _contained_by(region: dict[str, int], monitor: dict[str, int]) -> bool:
    return (
        region["left"] >= monitor["left"]
        and region["top"] >= monitor["top"]
        and region["left"] + region["width"] <= monitor["left"] + monitor["width"]
        and region["top"] + region["height"] <= monitor["top"] + monitor["height"]
    )


class CaptureWorker(QObject):
    """Move to a QThread before invoking slots through queued Qt signals.

    ``captured_at`` is Unix seconds; ``elapsed_ms`` measures capture plus the
    image copy. No OCR work, full-desktop capture, or GPU work occurs here.
    """

    frame_ready = Signal(QImage, dict)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer: QTimer | None = None
        self._capture: Any = None
        self._region: dict[str, int] | None = None
        self._frame_number = 0

    def _validate_monitor(self, region: dict[str, int]) -> None:
        if self._capture is not None and not any(
            _contained_by(region, monitor) for monitor in self._capture.monitors[1:]
        ):
            raise ValueError("请将整个扫描框放在同一块显示器内。")

    @Slot(dict)
    def start_capture(self, region: dict[str, Any]) -> None:
        try:
            checked = normalize_region(region)
            if self._capture is not None:
                self._validate_monitor(checked)
                self._region = checked
                return

            # MSS stores thread-local Win32 handles: construct it in this slot,
            # never in the GUI-thread constructor.
            import mss

            self._capture = mss.mss()
            self._validate_monitor(checked)
            self._region = checked
            self._frame_number = 0
            self._timer = QTimer(self)
            self._timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._timer.setInterval(50)
            self._timer.timeout.connect(self._capture_frame)
            self._timer.start()
            self._capture_frame()
        except Exception as error:
            self._cleanup()
            self.failed.emit(f"无法开始截图：{error}")

    @Slot(dict)
    def capture_once(self, request: dict[str, Any]) -> None:
        """One clean frame, after the controller temporarily hides its layers."""
        capture = None
        try:
            region = normalize_region(request)
            import mss
            capture = mss.mss()
            if not any(_contained_by(region, monitor) for monitor in capture.monitors[1:]):
                raise ValueError("请将整个扫描框放在同一块显示器内。")
            started = time.perf_counter()
            shot = capture.grab(region)
            if (shot.width != region["width"] or shot.height != region["height"]
                    or len(shot.bgra) < shot.width * shot.height * 4):
                raise RuntimeError("截图尺寸或像素缓冲不完整。")
            frame = QImage(shot.bgra, shot.width, shot.height, shot.width * 4,
                           QImage.Format.Format_RGB32).copy()
            if frame.isNull() or frame.width() != region["width"] or frame.height() != region["height"]:
                raise RuntimeError("截图尺寸不一致。")
            self._frame_number += 1
            metadata = {**region, "region": region, "elapsed_ms": (time.perf_counter()-started)*1000,
                        "captured_at": time.time(), "frame_number": self._frame_number,
                        "remote_capture_id": request.get("remote_capture_id"),
                        'hidden_layers': request.get('hidden_layers', []),
                        'compositor_flushed': request.get('compositor_flushed')}
            capture.close()
            capture = None
            self.frame_ready.emit(frame, metadata)
        except Exception as error:
            self.failed.emit(f"截图失败：{error}")
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    pass

    @Slot(dict)
    def set_region(self, region: dict[str, Any]) -> None:
        try:
            checked = normalize_region(region)
            self._validate_monitor(checked)
            self._region = checked
        except (TypeError, ValueError) as error:
            self.failed.emit(str(error))

    @Slot()
    def _capture_frame(self) -> None:
        if self._capture is None or self._region is None:
            return
        try:
            started = time.perf_counter()
            region = dict(self._region)
            screenshot = self._capture.grab(region)
            if (screenshot.width != region["width"] or screenshot.height != region["height"]
                    or len(screenshot.bgra) < screenshot.width * screenshot.height * 4):
                raise RuntimeError("截图尺寸或像素缓冲不完整。")
            # RGB32 consumes BGRA bytes on little-endian Windows and ignores
            # the source alpha channel. copy() detaches the cross-thread image
            # from MSS's temporary buffer before the signal is delivered.
            frame = QImage(
                screenshot.bgra,
                screenshot.width,
                screenshot.height,
                screenshot.width * 4,
                QImage.Format.Format_RGB32,
            ).copy()
            if frame.isNull():
                raise RuntimeError("截图图像为空。")
            self._frame_number += 1
            metadata = {
                **region,
                "region": region,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "captured_at": time.time(),
                "frame_number": self._frame_number,
            }
            self.frame_ready.emit(frame, metadata)
        except Exception as error:
            self._cleanup()
            self.failed.emit(f"截图失败：{error}")

    def _cleanup(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        if self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                # Keep the original capture failure available to the GUI and
                # always finish releasing the worker's references on shutdown.
                pass
            finally:
                self._capture = None

    @Slot()
    def stop_capture(self) -> None:
        try:
            self._cleanup()
        except Exception as error:
            self.failed.emit(f"停止截图时出错：{error}")
        finally:
            self.stopped.emit()
