"""The packaged trial uses portable reviewed plans and exportable evidence."""
import json
import zipfile
from dataclasses import replace

from PySide6.QtGui import QImage

from lisha_overlay.analysis import PlayerState
from lisha_overlay import __version__
from lisha_overlay.live_strategy import evaluate_current, evaluate_exact
from lisha_overlay.overlay import MarksOverlay
from lisha_overlay.solver import simulate_plan
from lisha_overlay.support import export_bundle, record_environment
from lisha_overlay.team_layout import Annotation, TeamLayout


def test_portable_native_policy_matches_reported_scene_in_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    values = [-1,-1,1,-1,1,1,-1,-1,-1,1,1,1,1,1,1,1,1,-1,-1,-1,-1,-1,-1,-1,-1]
    players = [PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if value>0 else 'black', abs(value))
               for i, value in enumerate(values)]
    minimum = evaluate_current(players)
    exact = evaluate_exact(players)
    assert minimum.instruction == '1~5人 [黑1] 吃扇\n三人 [黑1] 引导'
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
