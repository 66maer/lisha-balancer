"""Record actual owned-app capture inputs and results, without desktop input."""
from pathlib import Path
import hashlib
import json
import sys
import faulthandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtCore import QTimer, Qt, QObject, Slot
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from lisha_overlay import app as application


def main():
    target = ROOT / "artifacts" / "audit-live-input-private"
    target.mkdir(exist_ok=True)
    settings = application.load_settings()
    application.load_settings = lambda: settings
    application.save_settings = lambda value: None
    application.settings_path = lambda: target / "settings.json"
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    controller = application.OverlayController(app)
    frames, results = [], []

    def report():
        value = {"frames": frames, "results": results,
                 "distinct_input_frames": len({f["sha256"] for f in frames})}
        (target / "report.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def finish():
        report()
        app.quit()

    def frame(image, metadata):
        index = len(frames)
        path = target / f"frame-{index:03d}.png"
        image.save(str(path))
        data = bytes(image.constBits())
        frames.append({"index": index, "frame_number": metadata.get("frame_number"),
                       "sha256": hashlib.sha256(data).hexdigest()})
        if len(frames) >= 40:
            QTimer.singleShot(100, finish)

    def result(value, players, metadata):
        results.append({"frame_number": metadata.get("frame_number"), "status": value.status,
                        "unknown": metadata.get("unknown_keys"),
                        "players": [{"key": p.player_key, "color": p.color,
                                     "stacks": p.stacks, "alive": p.alive} for p in players]})

    class Recorder(QObject):
        @Slot(QImage, dict)
        def capture(self, image, metadata):
            frame(image, metadata)
        @Slot(object, object, object)
        def analyzed(self, value, players, metadata):
            result(value, players, metadata)
            report()
    recorder = Recorder(app)
    controller.worker.frame_ready.connect(recorder.capture, Qt.ConnectionType.QueuedConnection)
    controller.analysis_worker.completed.connect(recorder.analyzed, Qt.ConnectionType.QueuedConnection)
    QTimer.singleShot(200, lambda: controller.analyze_mode("all"))
    QTimer.singleShot(20000, finish)
    trace = (target / "watchdog.log").open("w", encoding="utf-8")
    faulthandler.dump_traceback_later(25, file=trace)
    app.exec()
    controller.shutdown()
    faulthandler.cancel_dump_traceback_later()
    trace.close()
    report()
    print(json.dumps({"frames": len(frames), "distinct": len({f["sha256"] for f in frames}),
                      "unknown_counts": [len(r["unknown"] or []) for r in results]}))


if __name__ == "__main__":
    main()
