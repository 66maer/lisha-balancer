"""An owned, native-pixel reference fixture for live screenshot integration QA."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget
from lisha_overlay.platform_win import native_client_region


class Reference(QWidget):
    def __init__(self, filename):
        super().__init__()
        self.image = QImage(filename)
        if self.image.isNull():
            raise ValueError("无法读取参考图")
        self.setWindowTitle("戾煞 · 参考画面（1:1）")
        scale = self.screen().devicePixelRatio()
        self.setFixedSize(round(self.image.width() / scale), round(self.image.height() / scale))
        self.move(820, 280)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.record_geometry)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.image)

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "timer"):
            self.timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def record_geometry(self):
        root = Path(__file__).resolve().parents[1] / "artifacts"
        root.mkdir(exist_ok=True)
        data = native_client_region(self)
        data["dpr"] = self.devicePixelRatioF()
        (root / "reference-geometry.json").write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    app = QApplication([])
    window = Reference(sys.argv[1])
    window.show()
    raise SystemExit(app.exec())
