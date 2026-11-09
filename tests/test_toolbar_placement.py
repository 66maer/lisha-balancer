from PySide6.QtCore import QPoint, QRect, QSize

from lisha_overlay.app import panel_placement


def test_attached_toolbar_goes_above_instead_of_covering_scan_data():
    scan = QRect(100, 200, 360, 480)
    placed = panel_placement(scan, QSize(232, 86), QRect(0, 0, 1920, 1080))
    assert placed == QRect(100, 110, 232, 86)
    assert not placed.intersects(scan)


def test_top_edge_uses_external_side_and_detached_drag_stays_outside():
    scan = QRect(-1800, 0, 360, 480)
    bounds = QRect(-1920, 0, 1920, 1080)
    placed = panel_placement(scan, QSize(232, 86), bounds)
    assert bounds.contains(placed) and not scan.intersects(placed)
    detached = panel_placement(scan, QSize(232, 86), bounds, QPoint(-1700, 150))
    assert bounds.contains(detached) and not scan.intersects(detached)


def test_full_display_scan_reports_no_external_placement():
    bounds = QRect(0, 0, 1280, 720)
    assert panel_placement(bounds, QSize(232, 86), bounds) is None


def test_growing_instruction_row_stays_attached_and_outside_game_data(qt_app):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from lisha_overlay.app import OverlayController
    from lisha_overlay.panel import ControlPanel
    panel=ControlPanel()
    scan=QRect(100,200,360,480)
    panel.move(100,88)
    panel.setFixedHeight(126)
    assert panel.geometry().intersects(scan)
    controller=SimpleNamespace(panel=panel,scan=SimpleNamespace(geometry=lambda:scan,
        screen=lambda:SimpleNamespace(availableGeometry=lambda:QRect(0,0,1920,1080))),
        attached=True,_quitting=False,_placing_panel=False,_save_timer=Mock())
    controller._dock_panel=lambda:OverlayController._dock_panel(controller)
    OverlayController._panel_resized(controller)
    assert controller.attached and not panel.geometry().intersects(scan)
    panel.deleteLater()
