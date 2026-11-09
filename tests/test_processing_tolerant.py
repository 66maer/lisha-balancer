"""Reference calculation remains available without manual T/isolation gates."""
from types import ModuleType
import sys

import pytest
from PySide6.QtGui import QImage

from lisha_overlay.processing import AnalysisWorker
from lisha_overlay.team_layout import TeamLayout
from lisha_overlay.analysis import PlayerState


def _run(monkeypatch, red, black, *, tank, auto_confirmed, mode="all"):
    players = [PlayerState(f"g{i//5+1}p{i%5+1}",
                           "red" if i < red else "black" if i < red+black else "none",
                           1 if i < red+black else 0, is_tank=tank and i == 0) for i in range(25)]
    class Vision:
        backend = "learned"

        def __init__(self, *args):
            self.diagnostics = {p.player_key: {
                "isolation_confirmed": auto_confirmed,
                "estimate_reasons": [] if auto_confirmed else ["隔绝图案证据不足"],
            } for p in players}
        def analyze(self, image):
            return list(players)
    module = ModuleType("lisha_overlay.vision")
    module.VisionEngine = Vision
    monkeypatch.setitem(sys.modules, "lisha_overlay.vision", module)
    worker = AnalysisWorker()
    results, errors = [], []
    worker.completed.connect(lambda result, people, metadata: results.append((result, metadata)))
    worker.failed.connect(lambda message, metadata: errors.append(message))
    worker.process(QImage(), {}, {"generation": 1, "layout": TeamLayout().to_dict(),
                                 "tank_key": "g1p1" if tank else None, "mode": mode})
    assert not errors
    assert len(results) == 1
    return results[0]


def test_automatic_isolation_evidence_removes_the_old_manual_confirmation_gate(monkeypatch):
    result, metadata = _run(monkeypatch, 16, 8, tank=True, auto_confirmed=True)
    assert result.status == "solved" and result.annotations
    assert not result.warnings and not metadata["isolation_unconfirmed_keys"]
    assert "隔绝待核" not in result.summary


def test_advisory_isolation_keeps_learned_candidates_with_reference_marks(monkeypatch):
    result, metadata = _run(monkeypatch, 16, 8, tank=True, auto_confirmed=False)
    assert result.status == "solved" and result.annotations
    assert result.reference and result.warnings
    assert "隔绝图案证据不足" in result.details
    assert len(metadata["reference_keys"]) == 25
    assert len(metadata["isolation_unconfirmed_keys"]) == 25
    assert metadata["vision_backend"] == "learned"
    assert metadata["model_output_policy"] == "best_current_prediction"


@pytest.mark.parametrize("mode", ["all", "fan", "circle"])
def test_current_snapshot_runs_all_modes_without_tank_notices(monkeypatch, mode):
    result, metadata = _run(monkeypatch, 12, 12, tank=False, auto_confirmed=True, mode=mode)
    assert result.status == "solved" and result.balanced and result.annotations==[]
    assert result.plan.current_snapshot and not result.plan.reference_without_tank
    assert result.current_red==12 and result.current_black==12
    assert 'T' not in result.summary and not result.warnings
    assert not metadata["reference_without_tank"]
