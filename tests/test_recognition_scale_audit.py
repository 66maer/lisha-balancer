"""The scale audit preserves labels/geometry and reports missing information."""
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QRectF

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_recognition_scales import (
    implementation_fingerprints,
    padded_translation,
    resolution_variants,
    score_players,
)
from lisha_overlay.team_layout import TeamLayout
from lisha_overlay.vision import VisionEngine


def test_fingerprints_cover_deployed_model_and_recognition_implementation():
    fingerprints = implementation_fingerprints(VisionEngine)
    expected = {
        "src/lisha_overlay/vision.py",
        "src/lisha_overlay/learned_recognition.py",
        "src/lisha_overlay/learned_geometry.py",
        "src/lisha_overlay/assets/learned/model.onnx",
        "src/lisha_overlay/assets/learned/configuration.json",
        "src/lisha_overlay/assets/buffs/ui_dead.png",
    }
    normalized = {name.replace("\\", "/") for name in fingerprints}
    assert expected <= normalized
    assert all(len(digest) == 64 for digest in fingerprints.values())


def test_integer_translation_keeps_every_source_pixel_and_reprojects_cells():
    image = np.random.default_rng(3).integers(0,256,(180,120,3),dtype=np.uint8)
    layout = TeamLayout.uniform(formation=(1,))
    moved, projected = padded_translation(image,layout,1,-1,padding=3)
    assert np.array_equal(moved[2:182,4:124],image)
    before = layout.cells(QRectF(0,0,120,180))
    after = projected.cells(QRectF(0,0,126,186))
    for original,result in zip(before,after):
        assert result.rect.x() == pytest.approx(original.rect.x()+4)
        assert result.rect.y() == pytest.approx(original.rect.y()+2)
        assert result.rect.width() == pytest.approx(original.rect.width())
        assert result.rect.height() == pytest.approx(original.rect.height())


def test_translation_refuses_offsets_that_would_remove_source_pixels():
    with pytest.raises(ValueError,match="cannot crop"):
        padded_translation(np.zeros((100,100,3),np.uint8),TeamLayout(),4,0,padding=3)


def test_two_stage_video_keeps_information_limit_even_when_display_size_recovers():
    image = np.random.default_rng(4).integers(0,256,(300,200,3),dtype=np.uint8)
    original = image.copy()
    variants = list(resolution_variants(image,TeamLayout(),full=False))
    video = next(item for item in variants if item.name == "video_0.65_display_1")
    assert video.image.shape == image.shape
    assert video.final_scale == (1.,1.)
    assert video.information_scale == pytest.approx((.65,.65))
    assert not np.array_equal(video.image,image)
    assert np.array_equal(original,image)


def test_scale_uses_actual_rounded_geometry_and_retains_normalized_layout():
    image = np.zeros((505,345,3),np.uint8)
    layout = TeamLayout()
    variant = next(item for item in resolution_variants(image,layout) if item.name == "scale_0.8")
    assert variant.image.shape[:2] == (404,276)
    assert variant.layout is layout
    before = layout.cells(QRectF(0,0,345,505))[0].rect
    after = layout.cells(QRectF(0,0,276,404))[0].rect
    assert after.x() == pytest.approx(before.x()*.8)
    assert after.height() == pytest.approx(before.height()*.8)


def _player(color="none",stacks=0,alive=True,isolated=False):
    return SimpleNamespace(player_key="g1p1",color=color,stacks=stacks,alive=alive,isolated=isolated)


def test_scoring_separates_rejection_and_confidently_wrong_life_state():
    truth = [{"key":"g1p1","color":"black","stacks":2,"alive":True}]
    rejected = score_players([_player("unknown",None)],truth,{})
    assert rejected["unknown_buff_cells"] == 1
    assert rejected["wrong_accepted_cells"] == 0
    assert not rejected["complete_buff_frame"]
    dead_truth = [{"key":"g1p1","color":"none","stacks":0,"alive":False}]
    wrong = score_players([_player()],dead_truth,{})
    assert wrong["wrong_accepted_cells"] == 1
    assert wrong["unknown_buff_cells"] == 0


def test_unconfirmed_isolation_is_not_silently_scored_as_correct():
    truth = [{"key":"g1p1","color":"none","stacks":0,"alive":True,"isolated":True}]
    result = score_players([_player(isolated=False)],truth,{"g1p1":{"isolation_confirmed":False}})
    assert result["unconfirmed_isolation_cells"] == 1
    assert result["wrong_accepted_cells"] == 0
    assert result["complete_buff_frame"]
    assert not result["complete_all_labelled_fields"]
    assert result["differences"][0]["expected_isolated"] is True
    assert result["differences"][0]["isolation_confirmed"] is False


def test_confidently_wrong_isolation_is_reported_as_accepted_error():
    truth = [{"key":"g1p1","color":"none","stacks":0,"alive":True,"isolated":True}]
    result = score_players([_player(isolated=False)],truth,{"g1p1":{"isolation_confirmed":True}})
    assert result["unconfirmed_isolation_cells"] == 0
    assert result["wrong_accepted_cells"] == 1
    assert result["differences"][0]["mismatch_fields"] == ["isolated"]
    assert not result["complete_all_labelled_fields"]


def test_missing_or_duplicate_player_never_passes_whole_team_scoring():
    truth = [{"key":"g1p1","color":"none","stacks":0,"alive":True}]
    assert not score_players([],truth,{})["complete_buff_frame"]
    duplicate = score_players([_player(),_player()],truth,{})
    assert duplicate["duplicate_keys_count"] == 1
    assert not duplicate["complete_buff_frame"]
