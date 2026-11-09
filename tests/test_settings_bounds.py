from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QDialogButtonBox, QScrollArea

from lisha_overlay.layout_preferences import LayoutPreferencesDialog
from lisha_overlay.overlay import ScanOverlay


def test_fine_grid_controls_scroll_and_dialog_actions_stay_visible(qt_app):
    scan = ScanOverlay()
    dialog = LayoutPreferencesDialog(qt_app.screens(), scan, 0.95, 0.9, False)
    dialog.ensurePolished()
    dialog.resize(650, 530)
    dialog.layout().activate()
    buttons = dialog.findChild(QDialogButtonBox)
    rect = QRect(buttons.mapTo(dialog, QPoint()), buttons.size())
    assert dialog.rect().contains(rect)
    assert dialog.findChild(QScrollArea, "gridControlsScroll").widgetResizable()
    assert not dialog.isolation_box.isChecked()
    dialog.deleteLater()
    scan.markers.deleteLater()
    scan.deleteLater()
