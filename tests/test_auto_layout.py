import pytest
from PySide6.QtCore import QRect, QRectF

from lisha_overlay.auto_layout import rebase_detected_layout, scan_geometry_for_bounds
from lisha_overlay.platform_win import _map_rect_to_monitor
from lisha_overlay.team_layout import TeamLayout


@pytest.mark.parametrize("formation", [(5,), (3, 2), (2, 3), (1, 4), (4, 1)])
@pytest.mark.parametrize("scale,logical,physical", [
    (1., QRect(0, 0, 1920, 1080), {"left": 0, "top": 0, "width": 1920, "height": 1080}),
    (1.5, QRect(-1920, 0, 1280, 720), {"left": -1920, "top": 0, "width": 1920, "height": 1080}),
    (1.25, QRect(0, -1200, 1920, 960), {"left": 0, "top": -1200, "width": 2400, "height": 1200}),
])
def test_detected_cells_keep_physical_positions_after_tight_crop(formation, scale, logical, physical):
    layout = TeamLayout.uniform(formation, left=.10, top=.08, right=.60, bottom=.25)
    source_cells = layout.cells(QRectF(0, 0, physical["width"], physical["height"]))
    bounds = QRectF(source_cells[0].rect)
    for cell in source_cells[1:]:
        bounds = bounds.united(cell.rect)
    geometry = scan_geometry_for_bounds(bounds, logical, scale)
    target = _map_rect_to_monitor(geometry.adjusted(4, 4, -4, -4), logical, physical, scale)
    cropped = rebase_detected_layout(layout, physical, target)
    for old, new in zip(source_cells, cropped.cells(QRectF(0, 0, target["width"], target["height"]))):
        assert new.key == old.key
        assert old.rect.x() + physical["left"] == pytest.approx(new.rect.x() + target["left"])
        assert old.rect.y() + physical["top"] == pytest.approx(new.rect.y() + target["top"])
        assert old.rect.width() == pytest.approx(new.rect.width())
        assert old.rect.height() == pytest.approx(new.rect.height())


def test_grid_against_monitor_edge_keeps_its_first_pixels_inside_capture():
    logical = QRect(-1920, 0, 1920, 1080)
    physical = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
    scan = scan_geometry_for_bounds(QRectF(0, 0, 350, 510), logical, 1.)
    captured = _map_rect_to_monitor(scan.adjusted(4, 4, -4, -4), logical, physical, 1.)
    assert captured["left"] == -1920 and captured["top"] == 0
    assert captured["width"] >= 350 and captured["height"] >= 510


def test_crop_rejects_a_detected_column_outside_actual_native_region():
    source = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    with pytest.raises(ValueError, match="未完整包含"):
        rebase_detected_layout(TeamLayout(), source, {**source, "width": 300})
