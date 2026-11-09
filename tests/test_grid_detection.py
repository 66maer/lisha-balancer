"""Automatic alignment fixtures contain no Buff templates or desktop input."""

import os

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from lisha_overlay.grid_detection import detect_team_grid


def _qimage(pixels):
    return QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0],
                  QImage.Format.Format_BGR888).copy()


def _draw_grid(pixels, formation, *, x=30, y=40, scale=1.0):
    width, row_height = round(108 * scale), round(44 * scale)
    gap, footer = max(2, round(5 * scale)), round(20 * scale)
    expected = []
    group = 0
    for row, count in enumerate(formation):
        top = y + row * (row_height * 5 + footer)
        for column in range(count):
            left = x + column * (width + gap)
            expected.append(QRectF(left, top, width, row_height * 5))
            for slot in range(5):
                player_top = top + slot * row_height
                color = (143, 103, 61) if (slot + group) % 3 == 0 else (42, 125, 40)
                if (slot + group) % 7 == 0:
                    color = (9, 11, 12)  # Dead/dark rows keep their cell borders.
                cv2.rectangle(pixels, (left, player_top),
                              (left + width - 1, player_top + row_height - 1), (161, 180, 167), 1)
                cv2.rectangle(pixels, (left + 2, player_top + 2),
                              (left + width - 3, player_top + row_height - 3), color, -1)
                # Class icon and short name strokes are separate from Buffs.
                icon = max(5, round(row_height * 0.29))
                cv2.rectangle(pixels, (left + 3, player_top + 3),
                              (left + 3 + icon, player_top + 3 + icon), (185, 75, 164), -1)
                cv2.line(pixels, (left + icon + 8, player_top + 7),
                         (left + width // 2, player_top + 7), (142, 182, 175), 1)
                # The health/name seam is not mistaken for an extra player.
                seam = player_top + round(row_height * 0.42)
                cv2.line(pixels, (left + 2, seam), (left + width - 3, seam), (32, 75, 43), 1)
            bottom = top + row_height * 5
            cv2.rectangle(pixels, (left, bottom), (left + width - 1, bottom + footer - 1), (48, 55, 52), 1)
            cv2.rectangle(pixels, (left + 1, bottom + 1), (left + width - 2, bottom + footer - 2), (5, 8, 6), -1)
            group += 1
    return expected


def _assert_cells_match(detection, expected, image_size, tolerance=7):
    assert detection is not None
    width, height = image_size
    assert len(detection.layout.groups) == len(expected)
    assert len(detection.layout.cells(QRectF(0, 0, width, height))) == 25
    for group, reference in zip(detection.layout.groups, expected):
        x, y, w, h = group.rect
        actual = QRectF(x * width, y * height, w * width, h * height)
        assert actual.left() == pytest.approx(reference.left(), abs=tolerance)
        assert actual.top() == pytest.approx(reference.top(), abs=tolerance)
        assert actual.right() == pytest.approx(reference.right(), abs=tolerance)
        assert actual.bottom() == pytest.approx(reference.bottom(), abs=tolerance)


@pytest.mark.parametrize("formation", [(5,), (3, 2), (2, 3), (1, 4), (4, 1)])
def test_detects_all_supported_formations_without_any_buff(formation):
    pixels = np.full((620, 760, 3), (28, 34, 31), dtype=np.uint8)
    expected = _draw_grid(pixels, formation)
    detection = detect_team_grid(_qimage(pixels))
    _assert_cells_match(detection, expected, (760, 620))
    assert detection.layout.grid_options["formation"] == formation
    assert detection.confidence >= 0.75


@pytest.mark.parametrize("scale,origin", [(0.75, (140, 90)), (1.0, (200, 160)), (1.5, (70, 30))])
def test_translation_and_scaling_in_textured_background(scale, origin):
    rng = np.random.default_rng(734)
    pixels = rng.integers(12, 44, size=(930, 1050, 3), dtype=np.uint8)
    cv2.circle(pixels, (710, 460), 220, (57, 82, 96), 5)
    cv2.line(pixels, (5, 800), (1000, 50), (74, 70, 67), 3)
    expected = _draw_grid(pixels, (3, 2), x=origin[0], y=origin[1], scale=scale)
    detection = detect_team_grid(_qimage(pixels))
    _assert_cells_match(detection, expected, (1050, 930), tolerance=9)


@pytest.mark.parametrize("scale", [.65, .8, 1.25])
def test_video_resampling_preserves_original_pixel_coordinates(scale):
    pixels = np.full((620, 760, 3), (28, 34, 31), dtype=np.uint8)
    expected = _draw_grid(pixels, (3, 2))
    width, height = round(760 * scale), round(620 * scale)
    resized = cv2.resize(pixels, (width, height), interpolation=cv2.INTER_AREA
                         if scale < 1 else cv2.INTER_CUBIC)
    sx, sy = width / 760, height / 620
    projected = [QRectF(rect.x() * sx, rect.y() * sy,
                        rect.width() * sx, rect.height() * sy) for rect in expected]
    detection = detect_team_grid(_qimage(resized))
    # Use the same 9-source-pixel boundary tolerance as native scaled grids.
    _assert_cells_match(detection, projected, (width, height), tolerance=max(7, 9 * scale))
    assert detection.image_size == (width, height)


def test_two_equally_plausible_complete_panels_are_rejected():
    pixels = np.full((610, 900, 3), (28, 34, 31), dtype=np.uint8)
    _draw_grid(pixels, (3, 2), x=20, y=40)
    _draw_grid(pixels, (3, 2), x=480, y=40)
    assert detect_team_grid(_qimage(pixels)) is None


def test_repeated_short_buff_edges_do_not_become_team_column_boundaries():
    pixels = np.full((620, 760, 3), (28, 34, 31), dtype=np.uint8)
    expected = _draw_grid(pixels, (5,))
    for rect in expected:
        for slot in range(5):
            top = round(rect.top() + slot * 44)
            for offset in (12, 30, 48, 66, 84):
                x = round(rect.left()) + offset
                cv2.line(pixels, (x, top + 19), (x, top + 42), (230, 230, 230), 1)
    found = detect_team_grid(_qimage(pixels))
    _assert_cells_match(found, expected, (760, 620))


def test_incomplete_columns_and_random_scene_are_rejected():
    pixels = np.full((620, 760, 3), (28, 34, 31), dtype=np.uint8)
    _draw_grid(pixels, (2, 2))
    assert detect_team_grid(_qimage(pixels)) is None
    random = np.random.default_rng(82).integers(0, 190, size=(520, 700, 3), dtype=np.uint8)
    assert detect_team_grid(_qimage(random)) is None
    assert detect_team_grid(QImage()) is None


def test_detected_layout_reprojects_into_padded_roi_without_shift():
    pixels = np.full((620, 760, 3), (28, 34, 31), dtype=np.uint8)
    _draw_grid(pixels, (3, 2), x=120, y=70)
    detection = detect_team_grid(_qimage(pixels))
    assert detection is not None
    region = detection.bounds.adjusted(-8, -8, 8, 8)
    layout = detection.layout_for_region(region)
    original = detection.layout.cells(QRectF(0, 0, 760, 620))
    projected = layout.cells(QRectF(0, 0, region.width(), region.height()))
    for before, after in zip(original, projected):
        assert before.key == after.key
        assert after.rect.x() + region.x() == pytest.approx(before.rect.x())
        assert after.rect.y() + region.y() == pytest.approx(before.rect.y())
        assert after.rect.width() == pytest.approx(before.rect.width())
        assert after.rect.height() == pytest.approx(before.rect.height())
    with pytest.raises(ValueError):
        detection.layout_for_region(QRectF(0, 0, 50, 50))


@pytest.mark.parametrize("environment,expected", [
    ("LISHA_REFERENCE_IMAGE", QRectF(18, 25, 329, 480)),
    ("LISHA_FULL_REFERENCE_IMAGE", QRectF(150, 176, 328, 481)),
])
def test_private_real_references_when_explicitly_supplied(environment, expected):
    filename = os.environ.get(environment)
    if not filename:
        pytest.skip(f"Set {environment} for an opt-in private-image replay.")
    frame = QImage(filename)
    detection = detect_team_grid(frame)
    assert detection is not None
    assert detection.layout.grid_options["formation"] == (3, 2)
    assert detection.bounds.left() == pytest.approx(expected.left(), abs=7)
    assert detection.bounds.top() == pytest.approx(expected.top(), abs=7)
    assert detection.bounds.right() == pytest.approx(expected.right(), abs=7)
    assert detection.bounds.bottom() == pytest.approx(expected.bottom(), abs=7)
