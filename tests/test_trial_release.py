"""The packaged trial uses portable reviewed plans and exportable evidence."""
import json
import zipfile
from dataclasses import replace

import pytest

from PySide6.QtGui import QImage

from lisha_overlay.analysis import PlayerState
from lisha_overlay import __version__
from lisha_overlay.live_strategy import evaluate_current, evaluate_exact
from lisha_overlay.overlay import MarksOverlay
from lisha_overlay.solver import simulate_plan
from lisha_overlay.support import export_bundle, record_environment
from lisha_overlay.team_layout import Annotation, TeamLayout
from lisha_overlay.difficulty import settlement_level


def test_portable_native_policy_matches_reported_scene_in_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    values = [-1,-1,1,-1,1,1,-1,-1,-1,1,1,1,1,1,1,1,1,-1,-1,-1,-1,-1,-1,-1,-1]
    players = [PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if value>0 else 'black', abs(value))
               for i, value in enumerate(values)]
    minimum = evaluate_current(players)
    exact = evaluate_exact(players)
    assert minimum.instruction == '{黑1} × 1 吃扇\n{黑1} × 3 引导圈\n无需蹭圈'
    assert minimum.strategy['quota']['selected_count'] == 1
    assert (minimum.final_red, minimum.final_black) == (11, 13)
    assert (exact.final_red, exact.final_black) == (11, 11)
    assert exact.strategy['quota']['selected_count'] == 3
    assert simulate_plan(players, minimum.plan).valid
    assert simulate_plan(players, exact.plan).valid
    assert all(int(action.player_key[1]) <= 2 for action in minimum.plan.actions)


def test_extended_states_keep_a_verified_plan_and_reference_label():
    values = [-1,-1,1,-1,1,1,-1,-1,-1,1,1,1,1,1,1,1,1,-1,-1,-1,-1,-1,-1,-1,-1]
    original = [PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if value>0 else 'black', abs(value))
                for i, value in enumerate(values)]
    for replacement in (replace(original[0], isolated=True), replace(original[0], stacks=4),
                        replace(original[0], alive=False)):
        for mode in ('all', 'circle'):
            players = [replacement, *original[1:]]
            result = evaluate_current(players, mode)
            assert result.reference
            assert result.strategy['policy'] == 'verified_extended_state_fallback'
            if result.plan is None:
                assert mode == 'circle' and result.status == 'no_solution'
                continue
            assert result.status == 'solved'
            checked = simulate_plan(players, result.plan)
            assert checked.valid
            assert not any(a.player_key not in result.plan.centers and a.circles == (1, 2)
                           for a in result.plan.actions)


@pytest.mark.parametrize('difficulty,expected', [
    ('normal', ['safe', 'safe', 'safe', 'danger', 'danger', 'fatal']),
    ('heroic', ['safe', 'safe', 'danger', 'fatal', 'fatal', 'fatal']),
])
def test_difficulty_boundaries_are_symmetric(difficulty, expected):
    assert [settlement_level(delta, difficulty) for delta in range(6)] == expected
    assert [settlement_level(-delta, difficulty) for delta in range(6)] == expected


@pytest.mark.parametrize('extended', [False, True])
def test_heroic_plans_are_independently_safe_and_use_three_current_buff_commands(extended):
    players = [PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if i < 15 else 'black', 1) for i in range(25)]
    if extended:
        players[24] = replace(players[24], alive=False)
    result = evaluate_current(players, difficulty='heroic')
    assert result.status == 'solved'
    checked = simulate_plan(players, result.plan, tolerance=1)
    assert checked.valid and checked.difference <= 1
    assert (result.final_red, result.final_black) == (checked.red_count, checked.black_count)
    assert len(result.instruction.splitlines()) == 3
    assert '记' not in result.instruction and '扇前Buff' not in result.instruction


def test_single_circle_solution_wins_and_required_double_circles_remain_available():
    players = [PlayerState(f'g{i//5+1}p{i%5+1}', 'red', 1) for i in range(25)]
    result = evaluate_current(players)
    assert result.status == 'solved' and simulate_plan(players, result.plan).valid
    assert all(len(action.circles) <= 1 for action in result.plan.actions)
    players = [replace(p, stacks=2) if i < 15 else replace(p, color='black', stacks=1)
               for i, p in enumerate(players)]
    result = evaluate_current(players, difficulty='heroic')
    assert result.status == 'solved' and simulate_plan(players, result.plan, tolerance=1).valid
    assert any(len(action.circles) == 2 for action in result.plan.actions)
    assert all(len(action.circles) <= 2 for action in result.plan.actions)


def test_missing_guides_remain_on_the_middle_command_row():
    values = [3]*10+[-3]*10+[0]*5
    players = [PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if value > 0 else 'black' if value < 0 else 'none',
                           abs(value), isolated=i == 24) for i, value in enumerate(values)]
    result = evaluate_current(players)
    assert result.balanced and result.plan is None
    assert result.instruction.splitlines() == ['无需吃扇', '引导圈需另排', '无需蹭圈']


def test_saved_marker_image_preserves_the_clean_input_and_buff_area(qt_app):
    marks = MarksOverlay()
    try:
        marks.resize(383, 513)
        marks.set_team_layout(TeamLayout())
        image = QImage(375, 505, QImage.Format.Format_RGB32)
        image.fill(0xFF25394B)
        original = bytes(image.constBits())
        output = marks.annotated_frame(image, [Annotation('g1p1', 'fan'), Annotation('g1p2', 'circle', 1)])
        assert bytes(image.constBits()) == original
        assert bytes(output.constBits()) != original
        for cell in marks.team_layout.cells(image.size()):
            rect = cell.buff_rect.toAlignedRect()
            assert image.copy(rect) == output.copy(rect)
        assert output.size() == image.size()
    finally:
        marks.deleteLater()


def test_debug_archive_contains_environment_and_selected_evidence_only(qt_app, tmp_path):
    folder = tmp_path/'logs'
    record_environment(folder)
    environment = json.loads((folder/'environment.json').read_text(encoding='utf8'))
    assert environment['capture']['desktop_recording_visible'] is True
    assert environment['screens'] and environment['hardware']['logical_cpus']
    assert environment['version'] == __version__
    (folder/'latest-analysis.json').write_text('{"status":"solved"}', encoding='utf8')
    (folder/'app.log').write_text('test', encoding='utf8')
    (folder/'unrelated.txt').write_text('not a debug artifact', encoding='utf8')
    image = QImage(40, 40, QImage.Format.Format_RGB32)
    image.fill(0xFF101010)
    image.save(str(folder/'latest-input.png'))
    image.save(str(folder/'latest-marked.png'))
    target = export_bundle(folder, tmp_path/'support.zip')
    with zipfile.ZipFile(target) as archive:
        assert {'environment.json', 'latest-analysis.json', 'app.log', 'latest-input.png', 'latest-marked.png', 'manifest.json'} <= set(archive.namelist())
        assert 'unrelated.txt' not in archive.namelist()
        assert archive.testzip() is None
