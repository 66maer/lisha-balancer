"""Exercise the real Qt thread/timer using synthetic MSS frames only."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from unittest.mock import patch

import pytest
from PySide6.QtCore import (
    QMetaObject, QObject, QThread, Qt, Signal, Slot, qInstallMessageHandler,
)
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from lisha_overlay.capture import CaptureWorker


class _Commands(QObject):
    start = Signal(dict)
    change_region = Signal(dict)
    stop = Signal()


class _Events(QObject):
    def __init__(self):
        super().__init__()
        self.frames: list[tuple[QImage, dict]] = []
        self.errors: list[str] = []
        self.stops = 0
        self.receiver_threads: list[int] = []

    @Slot(QImage, dict)
    def frame(self, image: QImage, metadata: dict):
        self.frames.append((image, metadata))
        self.receiver_threads.append(threading.get_ident())

    @Slot(str)
    def error(self, message: str):
        self.errors.append(message)

    @Slot()
    def stopped(self):
        self.stops += 1


@dataclass
class _SyntheticScreenshot:
    width: int
    height: int
    bgra: bytearray


class _SyntheticMSS:
    """No access to any real monitor, window, or screenshot API."""

    def __init__(self):
        self.owner_thread = threading.get_ident()
        self.call_threads: list[int] = []
        self.closed = False
        self.raw_frames: list[_SyntheticScreenshot] = []
        self.monitors = [
            {"left": -1000, "top": -1000, "width": 2000, "height": 2000},
            {"left": -1000, "top": -1000, "width": 2000, "height": 2000},
        ]

    def grab(self, region):
        self.call_threads.append(threading.get_ident())
        assert not self.closed
        screenshot = _SyntheticScreenshot(
            region["width"], region["height"],
            bytearray(b"\x10\x20\x30\x00" * region["width"] * region["height"]),
        )
        self.raw_frames.append(screenshot)
        return screenshot

    def close(self):
        self.call_threads.append(threading.get_ident())
        self.closed = True


def _wait_until(predicate, timeout_ms=2500):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "Timed out waiting for a worker event."


def test_worker_region_lifecycle_and_thread_affinity(qt_app):
    main_thread = threading.get_ident()
    instances: list[_SyntheticMSS] = []
    messages: list[str] = []

    def factory():
        capture = _SyntheticMSS()
        instances.append(capture)
        return capture

    previous_handler = qInstallMessageHandler(
        lambda _level, _context, message: messages.append(message)
    )
    commands = _Commands()
    events = _Events()
    worker = CaptureWorker()
    thread = QThread()
    thread.setObjectName("captureWorkerIntegrationTest")
    worker.moveToThread(thread)
    commands.start.connect(worker.start_capture)
    commands.change_region.connect(worker.set_region)
    commands.stop.connect(worker.stop_capture)
    worker.frame_ready.connect(events.frame)
    worker.failed.connect(events.error)
    worker.stopped.connect(events.stopped)
    thread.finished.connect(worker.deleteLater)

    first = {"left": -100, "top": -50, "width": 16, "height": 12}
    second = {"left": 25, "top": 40, "width": 9, "height": 7}

    try:
        with patch("mss.mss", side_effect=factory):
            thread.start()
            commands.start.emit(first)
            # A second frame demonstrates that the timer, rather than only the
            # immediate first capture, is running in the worker event loop.
            _wait_until(lambda: len(events.frames) >= 2)
            assert not events.errors
            image, metadata = events.frames[0]
            assert (image.width(), image.height()) == (16, 12)
            assert metadata["region"] == first
            assert metadata["width"] == 16
            assert metadata["captured_at"] > 0
            assert metadata["elapsed_ms"] >= 0
            assert [item[1]["frame_number"] for item in events.frames[:2]] == [1, 2]
            assert instances[0].owner_thread != main_thread
            assert set(instances[0].call_threads) == {instances[0].owner_thread}
            assert set(events.receiver_threads) == {main_thread}

            # Mutating the synthetic producer's original buffer must not alter
            # a QImage already delivered to the GUI thread.
            instances[0].raw_frames[0].bgra[:] = b"\x00" * len(
                instances[0].raw_frames[0].bgra
            )
            assert image.pixelColor(0, 0).getRgb() == (48, 32, 16, 255)

            commands.change_region.emit(second)
            _wait_until(lambda: any(m["region"] == second for _, m in events.frames))
            changed = next(img for img, m in events.frames if m["region"] == second)
            assert (changed.width(), changed.height()) == (9, 7)

            commands.stop.emit()
            _wait_until(lambda: events.stops == 1)
            count_when_stopped = len(events.frames)
            QTest.qWait(260)
            assert len(events.frames) == count_when_stopped
            assert instances[0].closed

            commands.start.emit(first)
            _wait_until(lambda: len(events.frames) > count_when_stopped)
            assert len(instances) == 2
            assert events.frames[count_when_stopped][1]["frame_number"] == 1
            assert events.frames[count_when_stopped][1]["region"] == first
            commands.stop.emit()
            _wait_until(lambda: events.stops == 2)
            assert all(instance.closed for instance in instances)
            assert all(
                set(instance.call_threads) == {instance.owner_thread}
                for instance in instances
            )
            assert not events.errors
    finally:
        if thread.isRunning():
            QMetaObject.invokeMethod(
                worker, "stop_capture", Qt.ConnectionType.BlockingQueuedConnection
            )
            thread.quit()
            assert thread.wait(3000), "Capture worker failed to exit."
        qInstallMessageHandler(previous_handler)

    thread_warnings = [
        message for message in messages
        if any(term in message.lower() for term in ("thread", "timer", "qobject"))
    ]
    assert not thread_warnings, thread_warnings


@pytest.mark.parametrize("invalid_part", ["width", "height", "buffer"])
def test_incomplete_frame_is_rejected_before_qimage_reads_buffer(qt_app, invalid_part):
    capture = _SyntheticMSS()
    region = {"left": 0, "top": 0, "width": 16, "height": 12}
    screenshot = capture.grab(region)
    if invalid_part == "buffer":
        screenshot.bgra = screenshot.bgra[:-1]
    else:
        setattr(screenshot, invalid_part, getattr(screenshot, invalid_part) - 1)
    capture.grab = lambda _region: screenshot
    worker = CaptureWorker()
    events = _Events()
    worker.frame_ready.connect(events.frame)
    worker.failed.connect(events.error)
    try:
        with patch("mss.mss", return_value=capture):
            worker.start_capture(region)
        assert events.errors == ["截图失败：截图尺寸或像素缓冲不完整。"]
        assert not events.frames
        assert capture.closed
        assert worker._capture is None and worker._timer is None
    finally:
        worker.stop_capture()
        worker.deleteLater()
