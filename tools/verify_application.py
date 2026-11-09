"""Exercise the real controller and capture thread against an owned test surface."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from lisha_overlay import app as application


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(application._icon())
    # Tests must not alter the user's saved positions or opacity.
    application.load_settings = lambda: {}
    application.save_settings = lambda value: None
    area = app.primaryScreen().availableGeometry()

    class Surface(QWidget):
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#25394b"))

    surface = Surface(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    surface.setWindowTitle("戾煞采集 · 应用集成检查")
    surface.move(area.topLeft() + QPoint(30, 40))
    surface.resize(min(1000, area.width() - 60), min(700, area.height() - 80))
    surface.show()
    controller = application.OverlayController(app)
    controller.scan.setGeometry(surface.x() + 40, surface.y() + 45, 340, 450)
    results = {}

    def begin():
        controller.toggle_capture()

    def verify():
        try:
            assert controller.running and controller.locked
            assert not controller._frame.isNull()
            image = controller._frame
            points = [(12, 12), (image.width() // 2, image.height() // 2)]
            assert all(image.pixelColor(x, y) == QColor("#25394b") for x, y in points)
            results["frames_received"] = controller._metadata["frame_number"]
            results["capture_elapsed_ms"] = round(controller._metadata["elapsed_ms"], 2)
            results["region"] = controller._region
            results["lock_hotkey"] = controller.hotkeys.labels[controller.hotkeys.LOCK_ID]
            controller.open_preview()
            assert controller.preview._save_button.isEnabled()
            assert controller.preview._canvas._image.size() == image.size()
            controller.panel.attach_button.click()
            assert not controller.attached
            controller.panel.attach_button.click()
            assert controller.attached
            controller.toggle_visibility()
            assert not controller.panel.isVisible() and not controller.scan.isVisible()
            controller.restore_controls()
            assert controller.panel.isVisible() and not controller.locked
            controller.scan.setGeometry(QRect(-100000, -100000, 340, 450))
            assert not controller._update_region()
            assert not controller.running and not controller._region
            assert controller._frame.isNull()
            assert not controller.panel.save_button.isEnabled()
            assert not controller.preview._save_button.isEnabled()
            results["passed"] = True
        except Exception as error:
            results["passed"] = False
            results["error"] = repr(error)
        finally:
            controller.shutdown()
            surface.hide()
            out = Path(__file__).resolve().parents[1] / "artifacts"
            out.mkdir(exist_ok=True)
            (out / "application-verification.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(results, ensure_ascii=False, indent=2))
            app.exit(0 if results.get("passed") else 1)

    QTimer.singleShot(700, begin)
    QTimer.singleShot(2200, verify)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
