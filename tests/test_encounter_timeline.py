from dataclasses import replace

from PySide6.QtGui import QImage

from lisha_overlay.analysis import PlayerState
from lisha_overlay.encounter_timeline import EncounterTimeline
from lisha_overlay.live_strategy import evaluate_current, evaluate_exact
from lisha_overlay.processing import AnalysisWorker
from lisha_overlay.solver import simulate_plan
from lisha_overlay.team_layout import TeamLayout


def _roster():
    return [PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if i < 13 else 'black',
                        1 if i < 13 else 2, is_tank=i == 0) for i in range(25)]


def test_timeline_accepts_an_onset_only_after_a_clean_panel_and_resyncs_after_a_gap():
    timeline = EncounterTimeline()
    assert timeline.observe(0, 'active').key == 'syncing'
    assert timeline.observe(.5, 'active').key == 'syncing'
    assert timeline.observe(1, 'clean').key == 'syncing'
    assert timeline.observe(1, 'clean').key == 'syncing'  # duplicate
    assert timeline.observe(1.5, 'clean').key == 'waiting'
    assert timeline.observe(2, 'active').key == 'waiting'
    assert timeline.observe(2.5, 'clean').key == 'waiting'  # single false positive
    timeline.observe(3, 'active')
    assert timeline.observe(3.5, 'active').key == 'before_tank'
    assert timeline.started_at == 3
    timeline.observe(4, 'clean')
    assert timeline.observe(4.5, 'clean').key == 'before_tank'
    assert timeline.observe(8, 'active').key == 'syncing'
    assert timeline.started_at is None


def test_worker_switches_from_tank_forecast_to_observed_circles_and_rearms():
    players = _roster()

    class Vision:
        diagnostics = {p.player_key: {'localized': True} for p in players}
        backend = 'learned'

        def analyze(self, image):
            return players

    worker = AnalysisWorker()
    worker._new_engine = lambda *_: Vision()
    config = dict(generation=1, layout=TeamLayout().to_dict(), mode='all', snapshot_stage='before_tank',
                  tank_key='g1p1', auto_timeline=True, timeline_session=1)
    results = []
    errors = []
    worker.completed.connect(lambda result, roster, metadata: results.append((result, metadata)))
    worker.failed.connect(lambda error, metadata: errors.append(error))
    active = players
    clean = [replace(p, color='none', stacks=0) for p in players]
    expected = {0: 'syncing', .5: 'waiting', 2: 'before_tank', 42.5: 'tank_cast',
                46.5: 'after_tank', 47.5: 'fan_cast', 49.5: 'after_fan', 62: 'circle_1',
                64.5: 'circle_2', 66: 'circle_3', 67: 'settling', 79: 'waiting',
                80.5: 'before_tank'}
    for half_second in range(162):
        seconds = half_second * .5
        players = clean if seconds < 1.5 or 78.5 <= seconds < 80 else active
        if seconds == 46.5:
            players = [replace(p, color='red', stacks=4) if p.player_key == 'g1p2' else p for p in players]
            config['tank_key'] = 'g1p2'
            config['generation'] += 1
        worker.process(QImage(), {'captured_at': seconds}, config)
        assert not errors
        result, metadata = results[-1]
        if seconds in expected:
            assert metadata['timeline_phase'] == expected[seconds]
        if metadata['effective_mode'] is None:
            assert result.status == 'timeline_wait' and not result.annotations
        if seconds == 2:
            assert result.plan.snapshot_stage == 'before_tank' and not result.plan.current_snapshot
            assert simulate_plan(players, result.plan).final_states['g1p1'].value == 4
        if seconds == 46.5:
            assert result.plan.snapshot_stage == 'after_tank'
            assert simulate_plan([replace(p, is_tank=p.player_key == 'g1p2') for p in players],
                                 result.plan).final_states['g1p2'].value == 4
        if seconds == 49.5:
            assert result.plan.mode == 'circle' and result.plan.current_snapshot
    assert results[-1][1]['timeline_started_at'] == 80.


def test_tank_warning_preempts_scheme_even_when_another_cell_is_uncertain():
    players = _roster()
    players[0] = replace(players[0], stacks=2)
    players[-1] = replace(players[-1], color='unknown', stacks=None)
    for evaluator in (evaluate_current, evaluate_exact):
        result = evaluator(players, snapshot_stage='before_tank')
        assert result.status == 'tank_warning' and result.instruction == '换T，要炸'
        assert result.plan is None and not result.annotations
        assert evaluator(players, snapshot_stage='after_tank').status == 'needs_recognition'
        assert evaluator(players, 'circle', snapshot_stage='before_tank').status == 'needs_recognition'


def test_exact_and_tolerant_preferences_choose_different_valid_circle_plans():
    players = _roster()
    tolerant = evaluate_current(players, 'circle', difficulty='heroic')
    exact = evaluate_exact(players, 'circle', difficulty='heroic')
    assert (tolerant.difference, exact.difference) == (1, 0)
    assert tolerant.strategy['preference'] == 'minimum'
    assert exact.strategy['preference'] == 'exact'
    assert all(simulate_plan(players, result.plan, tolerance=1).valid for result in (tolerant, exact))
