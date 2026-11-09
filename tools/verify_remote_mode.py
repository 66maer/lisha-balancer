"""Native remote-visibility/clean-capture check on an owned canvas, no input."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget
import mss
import numpy as np
from lisha_overlay import app as application
from lisha_overlay.platform_win import native_client_region, set_remote_compatible


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    application.load_settings = lambda: {"remote_compatible": True, "grid_ready": True}
    application.save_settings = lambda value: None
    area = app.primaryScreen().availableGeometry()
    class Surface(QWidget):
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#25394b"))
    surface = Surface(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    surface.setWindowTitle("戾煞 · 远程兼容验证画布")
    surface.setGeometry(area.x()+30, area.y()+30, 1000, 700)
    surface.show()
    controller = application.OverlayController(app)
    controller.scan.setGeometry(surface.x()+40, surface.y()+150, 340, 450)
    controller.scan.set_test_enabled(True)
    result = {"passed": False, "keyboard_mouse_calls": 0}

    def verify():
        try:
            assert controller.running and not controller._frame.isNull()
            frame = controller._frame
            pixels = np.frombuffer(frame.constBits(), np.uint8).reshape(frame.height(), frame.bytesPerLine())
            pixels = pixels[:, :frame.width()*4].reshape(frame.height(), frame.width(), 4)[:, :, :3]
            result["all_overlay_pixels_absent_from_internal_capture"] = bool(np.all(pixels == [75, 57, 37]))
            assert result["all_overlay_pixels_absent_from_internal_capture"]
            api = ctypes.WinDLL("user32", use_last_error=True)
            api.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            api.GetWindowDisplayAffinity.restype = wintypes.BOOL
            result["display_affinities"] = {}
            for name, window in (("panel", controller.panel), ("scan", controller.scan), ("marks", controller.scan.markers)):
                affinity = wintypes.DWORD()
                assert window.isVisible()
                assert api.GetWindowDisplayAffinity(int(window.winId()), ctypes.byref(affinity))
                result["display_affinities"][name] = affinity.value
                assert affinity.value == 0
            with mss.mss() as source:
                shot = source.grab(native_client_region(controller.panel))
                colors = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)[:, :, :3]
                result["toolbar_visible_in_desktop_capture"] = len(np.unique(colors.reshape(-1, 3), axis=0)) > 20
                assert result["toolbar_visible_in_desktop_capture"]
            result["on_demand_only"] = controller.worker._timer is None and controller._metadata["frame_number"] == 1
            assert result["on_demand_only"]
            result["passed"] = True
        except Exception as error:
            result["error"] = repr(error)
        finally:
            controller.shutdown()
            surface.hide()
            set_remote_compatible(False)
            target = ROOT / "artifacts/remote-verification.json"
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
            app.exit(0 if result["passed"] else 1)
    QTimer.singleShot(400, controller.toggle_capture)
    QTimer.singleShot(1800, verify)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
