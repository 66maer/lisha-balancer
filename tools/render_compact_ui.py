"""Render UI artifacts offscreen; never inject keyboard or mouse input."""
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from lisha_overlay.analysis import demo_result
from lisha_overlay.layout_preferences import LayoutPreferencesDialog
from lisha_overlay.overlay import ScanOverlay
from lisha_overlay.panel import ControlPanel
from lisha_overlay.team_layout import TeamLayout


app = QApplication([])
for filename in ("msyh.ttc", "msyhbd.ttc", "seguisym.ttf"):
    path = Path(os.environ["SystemRoot"]) / "Fonts" / filename
    if path.is_file():
        QFontDatabase.addApplicationFont(str(path))
root = Path(__file__).resolve().parents[1]
out = root / "artifacts"
out.mkdir(exist_ok=True)
frame = QImage(sys.argv[1]) if len(sys.argv) > 1 else QImage()
layout = TeamLayout()
panel = ControlPanel()
panel.set_attached(True)
panel.set_test_state(True)
panel.set_summary("测试 · 红1 9 黑1 7")
panel.ensurePolished()
panel.grab().save(str(out / "toolbar-v02.png"))
scan = ScanOverlay()
scan.resize(frame.width() + 8 if not frame.isNull() else 368,
            frame.height() + 8 if not frame.isNull() else 488)
scan.set_team_layout(layout)
scan.set_annotations(demo_result("all").annotations)
scan.set_test_enabled(True)
scan.markers.resize(scan.size())
if not frame.isNull():
    canvas = QImage(max(scan.width(), panel.width()) + 24, scan.height() + panel.height() + 24,
                    QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#111923"))
    painter = QPainter(canvas)
    painter.drawPixmap(QPoint(12, 6), panel.grab())
    origin = QPoint(12, panel.height() + 14)
    painter.drawImage(origin + QPoint(4, 4), frame)
    painter.drawPixmap(origin, scan.markers.grab())
    painter.drawPixmap(origin, scan.grab())
    painter.end()
    canvas.save(str(out / "overlay-v02.png"))
dialog = LayoutPreferencesDialog(app.screens(), scan, 0.96, 0.92, False,
                                 team_layout=layout, frame=frame, tank_key="g1p1")
dialog.ensurePolished()
dialog.resize(dialog.sizeHint())
dialog.grab().save(str(out / "settings-v02.png"))
buttons = dialog.findChild(QDialogButtonBox)
print("settings", dialog.size(), "minimum", dialog.minimumSizeHint(), "buttons", buttons.mapTo(dialog, QPoint()), buttons.size())
