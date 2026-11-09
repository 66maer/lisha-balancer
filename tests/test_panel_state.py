from PySide6.QtGui import QImage

from lisha_overlay.panel import ControlPanel, PreviewWindow
from lisha_overlay.preferences import PreferencesDialog
from lisha_overlay.overlay import ScanOverlay


def test_region_change_clears_stale_preview(qt_app):
    preview = PreviewWindow()
    frame = QImage(320, 240, QImage.Format.Format_RGB32)
    frame.fill(0xFF101010)
    preview.update_frame(frame, {"width": 320, "height": 240})
    assert preview._save_button.isEnabled()
    preview.clear_frame()
    assert preview._canvas._image.isNull()
    assert not preview._save_button.isEnabled()
    preview.deleteLater()


def test_controls_and_preferences_can_be_built_without_optional_qt_modules(qt_app):
    panel = ControlPanel()
    scan = ScanOverlay()
    dialog = PreferencesDialog(qt_app.screens(), scan, 0.96, 0.92, False)
    assert panel.sizeHint().width() <= 360
    assert panel.sizeHint().height() < 550
    assert not panel.save_button.isEnabled()
    assert dialog.width_box.value() >= 120
    panel.deleteLater()
    scan.deleteLater()
    dialog.deleteLater()
