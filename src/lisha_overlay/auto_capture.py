"""One-shot screen-region capture and automatic team-grid detection.

Move the worker to a QThread and call ``locate`` through a queued signal.
MSS resources are created and released in that same worker thread. The caller
must exclude its own overlay windows before requesting this frame. Detection
and capture never send keyboard/mouse input or enumerate application windows.
"""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage

from .capture import normalize_region


def _interrupted() -> bool:
    return QThread.currentThread().isInterruptionRequested()


def _inside(region: dict[str, int], monitor: dict[str, int]) -> bool:
    return (
        region["left"] >= monitor["left"] and region["top"] >= monitor["top"]
        and region["left"] + region["width"] <= monitor["left"] + monitor["width"]
        and region["top"] + region["height"] <= monitor["top"] + monitor["height"]
    )


class AutoGridWorker(QObject):
    completed = Signal(object, QImage, dict)
    failed = Signal(str, dict)

    @Slot(dict)
    def locate(self, config: dict[str, Any]) -> None:
        """Capture one physical region and detect the full grid within it.

        Required config: integer ``generation`` and physical-pixel rectangle
        ``capture_region`` with left/top/width/height. ``preferred_point`` and
        any other caller context are preserved in the returned metadata.
        Cancellation is silent; generation filtering remains the caller's job.
        """
        started = time.perf_counter()
        metadata = dict(config) if isinstance(config, dict) else {}
        capture = None
        try:
            if _interrupted():
                return
            if not isinstance(config, dict):
                raise ValueError("自动定位配置必须是对象。")
            generation = config.get("generation")
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise ValueError("自动定位 generation 必须是非负整数。")
            region = normalize_region(config.get("capture_region"))
            metadata["capture_region"] = region

            # These imports occur only when the slot runs, allowing the detector
            # implementation to be installed independently of the GUI module.
            import mss

            capture = mss.mss()
            monitors = capture.monitors[1:]
            if not monitors or not any(_inside(region, monitor) for monitor in monitors):
                raise ValueError("自动定位区域必须完整位于同一块显示器内。")
            if _interrupted():
                return
            screenshot = capture.grab(region)
            metadata["captured_at"] = time.time()
            if (screenshot.width != region["width"] or screenshot.height != region["height"]
                    or len(screenshot.bgra) < screenshot.width * screenshot.height * 4):
                raise RuntimeError("自动定位截图尺寸或像素缓冲不完整。")
            frame = QImage(screenshot.bgra, screenshot.width, screenshot.height,
                           screenshot.width * 4, QImage.Format.Format_RGB32).copy()
            if frame.isNull():
                raise RuntimeError("自动定位截图为空。")
            metadata['debug_frame'] = frame
            # No native screen resources are held during the image search.
            capture.close()
            capture = None
            if _interrupted():
                return

            from .grid_detection import detect_team_grid

            detection = detect_team_grid(frame)
            if _interrupted():
                return
            metadata["processing_ms"] = (time.perf_counter() - started) * 1000
            if detection is None:
                self.failed.emit("框内未找到完整25格，请完整框住团队面板后重新校准。", metadata)
            else:
                self.completed.emit(detection, frame, metadata)
        except Exception as error:
            # Release the thread-local native resources before publishing a
            # failure that may cause the caller to stop this worker thread.
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    pass
                capture = None
            if not _interrupted():
                metadata["processing_ms"] = (time.perf_counter() - started) * 1000
                self.failed.emit(f"自动定位失败：{error}", metadata)
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    pass
