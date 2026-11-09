"""Team-cell geometry and transparent overlay rendering without desktop access."""

import json

import pytest
from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter, QRegion
from PySide6.QtWidgets import QWidget

from lisha_overlay.overlay import MarksOverlay, ScanOverlay
from lisha_overlay.team_layout import Annotation, TeamLayout


def test_default_layout_has_25_distinct_in_bounds_cells():
    content = QRectF(0, 0, 455, 590)
    cells = TeamLayout().cells(content)
    assert len(cells) == 25
    assert len({cell.key for cell in cells}) == 25
    assert cells[0].key == "g1p1"
    assert cells[-1].key == "g5p5"
    for group in range(1, 6):
        assert [cell.slot for cell in cells if cell.group == group] == [1, 2, 3, 4, 5]
    for cell in cells:
        assert content.adjusted(-1e-8, -1e-8, 1e-8, 1e-8).contains(cell.rect)
        assert cell.rect.contains(cell.name_rect)
        assert cell.rect.contains(cell.buff_rect)
        assert cell.rect.contains(cell.marker_rect)


def test_cells_use_only_supplied_content_origin_and_scale():
    layout = TeamLayout()
    first = layout.cells(QSize(400, 600))[0]
    scaled = layout.cells(QRectF(17, 23, 800, 1200))[0]
    assert scaled.rect.x() == pytest.approx(17 + first.rect.x() * 2)
    assert scaled.rect.y() == pytest.approx(23 + first.rect.y() * 2)
    assert scaled.buff_rect.width() == pytest.approx(first.buff_rect.width() * 2)
    assert first.rect.x() == pytest.approx(400 * 0.045)
    assert first.rect.y() == pytest.approx(600 * 0.045)


def test_custom_calibration_round_trips_and_moves_name_region():
    config = TeamLayout().to_dict()
    config["groups"][0]["rect"] = [0.08, 0.06, 0.30, 0.42]
    config["name_rect"] = [0.10, 0.01, 0.85, 0.30]
    layout = TeamLayout.from_dict(json.loads(json.dumps(config)))
    assert layout.to_dict() == config
    cell = layout.cells(QSize(500, 800))[0]
    assert cell.rect == QRectF(40, 48, 150, 67.2)
    assert cell.name_rect.x() == pytest.approx(55)


def test_malformed_layout_rejects_duplicate_groups_and_invalid_normalized_rects():
    duplicate = TeamLayout().to_dict()
    duplicate["groups"][4]["group"] = 1
    for config in (
        [], duplicate, {"name_rect": [0, 0, 2, 1]},
        {"buff_rect": [0, 0, float("nan"), 1]}, {"groups": []},
    ):
        with pytest.raises(ValueError):
            TeamLayout.from_dict(config)


@pytest.mark.parametrize("formation", [(5,), (3, 2), (2, 3), (1, 4), (4, 1)])
def test_all_five_column_formations_keep_uniform_cells_in_bounds(formation):
    layout = TeamLayout.uniform(formation=formation)
    content = QRectF(0, 0, 375, 505)
    cells = layout.cells(content)
    assert len(cells) == 25
    assert {cell.group for cell in cells} == {1, 2, 3, 4, 5}
    assert all(cell.rect.width() == pytest.approx(cells[0].rect.width()) for cell in cells)
    assert all(cell.rect.height() == pytest.approx(cells[0].rect.height()) for cell in cells)
    assert all(content.adjusted(-1e-8, -1e-8, 1e-8, 1e-8).contains(cell.rect) for cell in cells)
    first_cells = [cell for cell in cells if cell.slot == 1]
    offset = 0
    for count in formation:
        row = first_cells[offset:offset + count]
        assert len({round(cell.rect.y(), 8) for cell in row}) == 1
        assert row[0].rect.left() == pytest.approx(content.width() * 0.045)
        offset += count


def test_grid_options_control_margins_gaps_and_footer_exactly():
    layout = TeamLayout.uniform(
        formation=(2, 3), left=0.05, top=0.06, right=0.03, bottom=0.02,
        column_gap=0.01, row_gap=0.04, footer=0.03,
    )
    cells = {cell.key: cell for cell in layout.cells(QSize(1000, 1000))}
    expected_width = (1000 - 50 - 30 - 2 * 10) / 3
    block_height = (1000 - 60 - 20 - 40) / 2
    assert cells["g1p1"].rect.width() == pytest.approx(expected_width)
    assert cells["g1p1"].rect.height() == pytest.approx((block_height - 30) / 5)
    assert cells["g2p1"].rect.left() - cells["g1p1"].rect.right() == pytest.approx(10)
    assert cells["g3p1"].rect.top() - cells["g1p5"].rect.bottom() == pytest.approx(40 + 30)
    assert cells["g5p5"].rect.bottom() == pytest.approx(1000 - 20 - 30)


@pytest.mark.parametrize("formation", [(1,), (8,), (4, 4), (1, 2, 3, 2)])
def test_debug_column_counts_one_through_eight_round_trip(formation):
    layout = TeamLayout.uniform(formation=formation)
    count = sum(formation)
    assert len(layout.cells(QSize(800, 600))) == count * 5
    restored = TeamLayout.from_dict(json.loads(json.dumps(layout.to_dict())))
    assert restored.grid_options["formation"] == formation
    assert restored.to_dict() == layout.to_dict()
    assert TeamLayout.uniform(**restored.grid_options).to_dict() == layout.to_dict()


@pytest.mark.parametrize("options", [
    {"formation": ()}, {"formation": (0, 5)}, {"formation": (4, 5)},
    {"formation": (True, 4)}, {"formation": (2.5, 2.5)},
    {"left": 0.6, "right": 0.5}, {"footer": 0.5},
    {"row_gap": float("nan")}, {"column_gap": -0.1}, {"top": 10 ** 500},
])
def test_invalid_uniform_layout_parameters_fail_before_rendering(options):
    with pytest.raises(ValueError):
        TeamLayout.uniform(**options)


def test_config_with_only_grid_options_builds_cells_and_legacy_groups_are_supported():
    layout = TeamLayout.from_dict({"grid_options": {"formation": [4, 1], "left": 0.06}})
    assert layout.grid_options["formation"] == (4, 1)
    assert len(layout.cells(QSize(375, 505))) == 25
    legacy = TeamLayout().to_dict()
    legacy.pop("grid_options")
    legacy["version"] = 1
    assert TeamLayout.from_dict(legacy).groups == TeamLayout().groups
    assert Annotation("g8p5", "circle", 3).player_key == "g8p5"
    with pytest.raises(ValueError):
        Annotation("g9p1", "fan")


def _render(widget: QWidget) -> QImage:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        widget.render(painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    finally:
        painter.end()
    return image


def test_scan_center_is_empty_native_region_and_has_no_painted_background(qt_app):
    scan = ScanOverlay()
    try:
        scan.resize(455, 590)
        image = _render(scan)
        assert not scan.border_region().contains(QPoint(200, 200))
        assert scan.border_region().contains(QPoint(2, 200))
        assert image.pixelColor(200, 200).alpha() == 0
        assert image.pixelColor(20, 20).alpha() == 0
        assert image.pixelColor(2, 50).alpha() > 0
        assert scan.markers.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert scan.markers.windowFlags() & Qt.WindowType.WindowTransparentForInput
        assert scan._hit_edges(QPoint(2, 2)) == "lt"
        assert scan._hit_edges(QPoint(2, 295)) == "l"
        assert scan._hit_edges(QPoint(2, 100)) == ""
    finally:
        scan.close()


def test_annotations_render_only_requested_cells_and_modes(qt_app):
    marks = MarksOverlay()
    try:
        marks.resize(455, 590)
        assert not any(bytes(_render(marks).constBits()))
        annotations = [Annotation("g1p2", "fan"), Annotation("g1p2", "circle", 1),
                       Annotation("g4p3", "circle", 2), Annotation("g5p4", "circle", 3)]
        marks.set_annotations(annotations)
        assert marks.displayed_annotations() == annotations
        image = _render(marks)
        assert any(bytes(image.constBits()))
        # A player's text area and unrelated empty cells remain transparent.
        cells = {cell.key: cell for cell in marks.team_layout.cells(marks.content_rect())}
        point = cells["g1p2"].name_rect.center().toPoint()
        assert image.pixelColor(point).alpha() == 0
        point = cells["g3p1"].marker_rect.center().toPoint()
        assert image.pixelColor(point).alpha() == 0
        marks.set_analysis_mode("fan")
        assert marks.displayed_annotations() == [annotations[0]]
        marks.set_analysis_mode("circle")
        assert marks.displayed_annotations() == annotations[1:]
        marks.set_test_enabled(True)
        # External demo annotations take precedence over the built-in fallback.
        assert marks.displayed_annotations() == annotations[1:]
        assert not marks._icon("fan").isNull()
        assert not marks._icon("circle").isNull()
        test_image = _render(marks)
        assert test_image != image
        marks.set_annotations([])
        assert {item.order for item in marks.displayed_annotations()} == {1, 2, 3}
        marks.set_test_enabled(False)
        assert not marks.displayed_annotations()
        assert not any(bytes(_render(marks).constBits()))
    finally:
        marks.close()
