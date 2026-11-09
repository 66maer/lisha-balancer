from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtWidgets import QApplication
from lisha_overlay.app import _icon

app = QApplication([])
target = Path(__file__).resolve().parents[1] / "assets" / "app.ico"
target.parent.mkdir(exist_ok=True)
if not _icon().pixmap(64, 64).save(str(target), "ICO"):
    raise RuntimeError("Unable to write application icon")
