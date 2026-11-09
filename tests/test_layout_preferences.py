from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialogButtonBox

from lisha_overlay.layout_preferences import LayoutPreferencesDialog
from lisha_overlay.overlay import ScanOverlay
from lisha_overlay.team_layout import TeamLayout


def make_dialog(qt_app):
    scan = ScanOverlay()
    frame = QImage(346, 505, QImage.Format.Format_RGB32)
    frame.fill(0xFF153040)
    dialog = LayoutPreferencesDialog(qt_app.screens(), scan, 0.9, 0.9, False,
                                     team_layout=TeamLayout(), frame=frame)
    return dialog, scan


def test_all_requested_formations_have_fixed_five_player_columns(qt_app):
    dialog, scan = make_dialog(qt_app)
    for formation in ("5", "3+2", "2+3", "1+4", "4+1"):
        dialog.formation_box.setCurrentText(formation)
        assert dialog.selected_layout.grid_options["formation"] == tuple(map(int, formation.split("+")))
        assert len(dialog.selected_layout.cells(QRectF(0, 0, 346, 505))) == 25
    dialog.deleteLater()
    scan.markers.deleteLater()
    scan.deleteLater()


def test_invalid_live_grid_cannot_be_committed_and_can_recover(qt_app):
    dialog, scan = make_dialog(qt_app)
    button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
    dialog.formation_box.setCurrentText("4+2")
    assert not button.isEnabled()
    dialog.formation_box.setCurrentText("3+2")
    assert button.isEnabled()
    dialog.rect_spins[2].setValue(100)
    assert not button.isEnabled()
    dialog.rect_spins[2].setValue(43)
    assert button.isEnabled()
    dialog.deleteLater()
    scan.markers.deleteLater()
    scan.deleteLater()


def test_preview_overrides_merge_and_can_be_cleared(qt_app):
    dialog, scan = make_dialog(qt_app)
    dialog._override_changed("g1p1", {"color": "red", "stacks": 3})
    dialog._override_changed("g1p1", {"isolated": True})
    assert dialog.overrides["g1p1"] == {"color": "red", "stacks": 3, "isolated": True}
    dialog._select_tank("g2p4")
    assert dialog.tank_box.currentData() == "g2p4"
    dialog._clear_overrides()
    assert not dialog.overrides
    dialog.deleteLater()
    scan.markers.deleteLater()
    scan.deleteLater()
