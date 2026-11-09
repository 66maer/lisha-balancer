"""Only synthetic MSS data is used; no real desktop or input API is called."""
from dataclasses import dataclass
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QRectF, QThread, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from lisha_overlay.auto_capture import AutoGridWorker


class Commands(QObject):
    locate = Signal(dict)


class Events(QObject):
    def __init__(self):
        super().__init__()
        self.results = []
        self.failures = []
        self.receiver_threads = []

    @Slot(object, QImage, dict)
    def completed(self, detection, frame, metadata):
        self.results.append((detection, frame, metadata))
        self.receiver_threads.append(threading.get_ident())

    @Slot(str, dict)
    def failed(self, reason, metadata):
        self.failures.append((reason, metadata))
        self.receiver_threads.append(threading.get_ident())


@dataclass
class Screenshot:
    width: int
    height: int
    bgra: bytearray


class SyntheticMSS:
    def __init__(self, *, grab_error=None, interrupt_on_grab=False):
        self.owner_thread = threading.get_ident()
        self.call_threads = []
        self.closed = False
        self.region = None
        self.image = None
        self.grab_error = grab_error
        self.interrupt_on_grab = interrupt_on_grab
        self.monitors = [
            {"left": -1000, "top": 0, "width": 2000, "height": 1000},
            {"left": -1000, "top": 0, "width": 1000, "height": 1000},
            {"left": 0, "top": 0, "width": 1000, "height": 1000},
        ]

    def grab(self, region):
        self.call_threads.append(threading.get_ident())
        self.region = dict(region)
        if self.grab_error:
            raise self.grab_error
        self.image = Screenshot(region["width"], region["height"],
                                bytearray(b"\x10\x20\x30\x00" * region["width"] * region["height"]))
        if self.interrupt_on_grab:
            QThread.currentThread().requestInterruption()
        return self.image

    def close(self):
        self.call_threads.append(threading.get_ident())
        self.closed = True


def wait_until(predicate, timeout=2500):
    deadline = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "Timed out waiting for automatic capture"


@pytest.fixture
def harness(qt_app, monkeypatch):
    instances = []
    factory_config = {}

    def factory():
        instance = SyntheticMSS(**factory_config)
        instances.append(instance)
        return instance

    import mss
    monkeypatch.setattr(mss, "mss", factory)
    detector_module = ModuleType("lisha_overlay.grid_detection")
    detector_module.detect_team_grid = lambda image: None
    monkeypatch.setitem(sys.modules, "lisha_overlay.grid_detection", detector_module)
    worker = AutoGridWorker()
    thread = QThread()
    worker.moveToThread(thread)
    commands = Commands()
    events = Events()
    commands.locate.connect(worker.locate)
    worker.completed.connect(events.completed)
    worker.failed.connect(events.failed)
    thread.finished.connect(worker.deleteLater)
    thread.start()
    yield SimpleNamespace(worker=worker, thread=thread, commands=commands,
                          events=events, instances=instances, detector=detector_module,
                          factory_config=factory_config)
    thread.quit()
    assert thread.wait(3000)


def config():
    return {"generation": 7, "capture_region": {"left": -120, "top": 40, "width": 8, "height": 6},
            "preferred_point": [-115, 43], "tag": "caller-context"}


def test_capture_detection_and_cleanup_stay_on_worker_thread(harness):
    gui_thread = threading.get_ident()
    detector_threads = []
    expected = SimpleNamespace(bounds=QRectF(1, 1, 6, 4), layout="layout", confidence=0.95, reason="fixture")

    def detect(image):
        detector_threads.append(threading.get_ident())
        assert harness.instances[0].closed
        assert image.pixelColor(0, 0).getRgb() == (48, 32, 16, 255)
        return expected

    harness.detector.detect_team_grid = detect
    request = config()
    harness.commands.locate.emit(request)
    wait_until(lambda: harness.events.results)
    assert not harness.events.failures
    detection, frame, metadata = harness.events.results[0]
    source = harness.instances[0]
    assert detection is expected and source.region == request["capture_region"]
    assert source.owner_thread != gui_thread
    assert source.call_threads and set(source.call_threads) == {source.owner_thread}
    assert detector_threads == [source.owner_thread]
    assert harness.events.receiver_threads == [gui_thread]
    assert metadata["generation"] == 7 and metadata["preferred_point"] == [-115, 43]
    assert metadata["tag"] == "caller-context" and metadata["captured_at"] > 0
    assert metadata["processing_ms"] >= 0
    source.image.bgra[:] = b"\x00" * len(source.image.bgra)
    assert frame.pixelColor(0, 0).getRgb() == (48, 32, 16, 255)


def test_not_found_has_exact_message_and_generation(harness):
    harness.commands.locate.emit(config())
    wait_until(lambda: harness.events.failures)
    reason, metadata = harness.events.failures[0]
    assert reason == "框内未找到完整25格，请完整框住团队面板后重新校准。" and metadata["generation"] == 7
    assert "captured_at" in metadata and metadata["processing_ms"] >= 0
    assert not harness.events.results and harness.instances[0].closed
    assert len(harness.instances)==1 and harness.instances[0].region==config()['capture_region']


@pytest.mark.parametrize("stage", ["grab", "detect"])
def test_errors_emit_context_and_release_capture(harness, stage):
    if stage == "grab":
        harness.factory_config["grab_error"] = RuntimeError("synthetic capture failure")
    else:
        def fail(_image):
            raise RuntimeError("synthetic detector failure")
        harness.detector.detect_team_grid = fail
    harness.commands.locate.emit(config())
    wait_until(lambda: harness.events.failures)
    reason, metadata = harness.events.failures[0]
    assert reason.startswith("自动定位失败：") and "synthetic" in reason
    assert metadata["generation"] == 7 and metadata["processing_ms"] >= 0
    assert harness.instances[0].closed and not harness.events.results


@pytest.mark.parametrize("region", [
    {"left": -5, "top": 20, "width": 10, "height": 10},
    {"left": -100, "top": 20, "width": 0, "height": 10},
])
def test_cross_monitor_or_invalid_region_never_grabs(harness, region):
    harness.commands.locate.emit({**config(), "capture_region": region})
    wait_until(lambda: harness.events.failures)
    assert not any(instance.region for instance in harness.instances)
    assert all(instance.closed for instance in harness.instances)


def test_interruption_after_capture_prevents_detection_and_signals(harness):
    harness.factory_config["interrupt_on_grab"] = True
    calls = []
    harness.detector.detect_team_grid = lambda image: calls.append(image)
    harness.commands.locate.emit(config())
    wait_until(lambda: harness.instances and harness.instances[0].closed)
    QTest.qWait(30)
    assert not calls and not harness.events.results and not harness.events.failures


def test_interruption_before_request_does_not_create_capture(harness):
    harness.thread.requestInterruption()
    harness.commands.locate.emit(config())
    QTest.qWait(50)
    assert not harness.instances and not harness.events.results and not harness.events.failures


def test_interruption_inside_detector_suppresses_outdated_result(harness):
    def detect(_image):
        QThread.currentThread().requestInterruption()
        return object()
    harness.detector.detect_team_grid = detect
    harness.commands.locate.emit(config())
    wait_until(lambda: harness.instances and harness.instances[0].closed)
    QTest.qWait(30)
    assert not harness.events.results and not harness.events.failures
