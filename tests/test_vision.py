"""Shared image boundaries and the production recognition entry point."""

from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

from lisha_overlay.team_layout import TeamLayout
from lisha_overlay.vision import VisionEngine, _pixel_rect, _qimage_bgr, _read_image


def test_qimage_conversion_preserves_channels_and_detaches_storage():
    frame = QImage(3, 2, QImage.Format.Format_RGB888)
    frame.fill(QColor(17, 83, 241))
    pixels = _qimage_bgr(frame)
    assert pixels.shape == (2, 3, 3)
    np.testing.assert_array_equal(pixels, np.tile([241, 83, 17], (2, 3, 1)))
    frame.fill(QColor(0, 0, 0))
    assert pixels[0, 0].tolist() == [241, 83, 17]
    pixels[:] = 255
    assert frame.pixelColor(0, 0) == QColor(0, 0, 0)


@pytest.mark.parametrize("frame", [None, QImage()])
def test_invalid_screenshots_are_rejected(frame):
    with pytest.raises(ValueError, match="截图"):
        _qimage_bgr(frame)


def test_pixel_rect_clamps_to_image_and_preserves_fractional_edges():
    image = np.zeros((20, 30, 3), np.uint8)
    assert _pixel_rect(QRectF(-1.2, 1.2, 7.1, 4.2), image) == (0, 1, 6, 6)
    assert _pixel_rect(QRectF(28.2, 18.4, 10, 10), image) == (28, 18, 30, 20)


def test_image_loader_handles_unicode_paths_and_missing_files(tmp_path):
    path = tmp_path / "截图.png"
    image = np.full((10, 10, 3), 75, np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    encoded.tofile(path)
    np.testing.assert_array_equal(_read_image(path), image)
    assert _read_image(tmp_path / "missing.png") is None


def test_default_engine_uses_deployed_model_even_with_old_environment_setting(monkeypatch):
    monkeypatch.setenv("LISHA_VISION_BACKEND", "legacy")
    engine = VisionEngine(TeamLayout())
    assert engine.backend == "learned"
    assert engine._learned_recognizer is not None
    assert not hasattr(engine, "_assets")
    with pytest.raises(ValueError, match="截图"):
        engine.analyze(QImage())


@pytest.mark.parametrize("backend", ["legacy", "references", "unsupported"])
def test_removed_recognition_backends_are_rejected(backend):
    with pytest.raises(ValueError, match="learned"):
        VisionEngine(TeamLayout(), backend=backend)


def test_engine_rejects_invalid_layout():
    with pytest.raises(TypeError, match="TeamLayout"):
        VisionEngine({})


@pytest.mark.parametrize("columns", [1, 5, 8])
def test_roster_size_and_tank_identity_follow_the_supplied_layout(columns):
    layout = TeamLayout.uniform(formation=(columns,))
    frame = QImage(columns * 115, 260, QImage.Format.Format_RGB32)
    frame.fill(QColor("#202020"))
    tank_key = f"g{columns}p5"
    engine = VisionEngine(layout, tank_key)
    players = engine.analyze(frame)
    assert len(players) == columns * 5
    assert [player.player_key for player in players if player.is_tank] == [tank_key]
    assert all(player.color == "unknown" for player in players)
    assert set(engine.diagnostics) == {player.player_key for player in players}


def test_death_text_fallback_and_its_cache_remain_available():
    engine = VisionEngine(TeamLayout())
    image = np.zeros((505, 650, 3), np.uint8)
    cell = engine.layout.cells(QRectF(0, 0, 650, 505))[14]
    asset = Path(__file__).resolve().parents[1] / "src/lisha_overlay/assets/buffs/ui_dead.png"
    sample = _read_image(asset)
    assert sample is not None
    left = round(cell.rect.right() - sample.shape[1] - 3)
    top = round(cell.rect.y() + cell.rect.height() * .43)
    image[top:top + sample.shape[0], left:left + sample.shape[1]] = sample
    first = engine._dead_player(image, cell)
    assert first[0] is True
    assert engine._dead_player(image, cell) == first
    assert cell.key in engine._dead_cache
