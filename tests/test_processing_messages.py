"""Buff recognition messages stay separate from grid calibration and isolation."""

from dataclasses import replace
from types import ModuleType
import sys

import pytest
from PySide6.QtGui import QImage

from lisha_overlay import processing
from lisha_overlay.analysis import AnalysisResult, PlayerState
from lisha_overlay.team_layout import TeamLayout


def _roster():
    return [PlayerState(f"g{i // 5 + 1}p{i % 5 + 1}", "none", 0, is_tank=i == 0)
            for i in range(25)]


def _process(monkeypatch, players):
    class FakeVision:
        diagnostics = {}

        def __init__(self, layout, tank_key):
            pass

        def analyze(self, image):
            return list(players)

    vision = ModuleType("lisha_overlay.vision")
    vision.VisionEngine = FakeVision
    monkeypatch.setitem(sys.modules, "lisha_overlay.vision", vision)
    worker = processing.AnalysisWorker()
    completed, failed = [], []
    worker.completed.connect(lambda result, roster, metadata: completed.append((result, metadata)))
    worker.failed.connect(lambda message, metadata: failed.append(message))
    try:
        worker.process(QImage(), {}, {
            "generation": 8, "layout": TeamLayout().to_dict(), "tank_key": "g1p1", "mode": "all",
        })
        assert not failed
        assert len(completed) == 1
        return completed[0]
    finally:
        worker.deleteLater()


def test_buff_type_and_stack_unknowns_are_people_with_separate_details(monkeypatch):
    players = _roster()
    players[1] = replace(players[1], color="unknown", stacks=None)
    players[7] = replace(players[7], color="red", stacks=None)
    players[8] = replace(players[8], color="black", stacks=None)
    players[13] = replace(players[13], color="unknown", stacks=None)
    players[24] = replace(players[24], alive=False, color="unknown", stacks=None)

    result, metadata = _process(monkeypatch, players)

    assert result.status == "needs_recognition"
    assert result.summary == "Buff待识别 · 4人"
    assert metadata["unknown_keys"] == ["g1p2", "g2p3", "g2p4", "g3p4"]
    assert "Buff 类型待确认：1队2、3队4" in result.details
    assert "Buff 层数待确认：2队3、2队4" in result.details
    assert "5队5" not in result.details
    assert "定位" not in result.summary + result.details
    assert not result.annotations


def test_unconfirmed_isolation_names_the_actual_uncertainty(monkeypatch):
    players = _roster()
    players[4] = replace(players[4], isolated=None)
    result, metadata = _process(monkeypatch, players)
    assert result.status == "needs_recognition"
    assert metadata["unknown_keys"] == []
    assert result.summary == "隔绝待确认 · 1人"
    assert metadata['unverified_isolation_keys'] == ['g1p5']
    assert "隔绝字样证据冲突" in result.details
    assert "定位" not in result.summary + result.details


@pytest.mark.parametrize("status,summary,details", [
    ("needs_calibration", "待校准", "需要完整的 25 个团队格子。"),
    ("needs_confirmation", "待核对隔绝", "请核对隔绝。"),
])
def test_buff_message_formatting_never_overrides_other_gates(monkeypatch, status, summary, details):
    players = _roster()
    players[1] = replace(players[1], color="unknown", stacks=None)
    monkeypatch.setattr(processing, "evaluate", lambda *args, **kwargs:
                        AnalysisResult(summary, status=status, details=details))
    result, metadata = _process(monkeypatch, players)
    assert metadata["unknown_keys"] == ["g1p2"]
    assert (result.status, result.summary, result.details) == (status, summary, details)
