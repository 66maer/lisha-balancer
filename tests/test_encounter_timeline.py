from dataclasses import replace

from PySide6.QtGui import QImage

from lisha_overlay.analysis import PlayerState
from lisha_overlay.encounter_timeline import AFTER_FAN, AFTER_TANK, BEFORE_TANK, SETTLING, TANK_CAST, EncounterTimeline
from lisha_overlay.encounter_timeline import MANUAL_CLEAR_DELAY, SETTLEMENT
from lisha_overlay.live_strategy import evaluate_current, evaluate_exact
from lisha_overlay.processing import AnalysisWorker, _timeline_evidence
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


def test_only_red_and_black_evidence_can_start_or_hold_a_round():
    players = [replace(p, color='none', stacks=0, isolated=True) for p in _roster()]
    flags = {p.player_key: {'localized': True} for p in players}
    flags['g1p1'].update(unverified_scale=True, unverified_buff_scale=False,
                        unverified_isolation=True, estimate_reasons=['隔绝图案证据不足'])
    assert _timeline_evidence(players, flags)[0] == 'clean'
    flags['g1p1']['unverified_buff_scale'] = True
    assert _timeline_evidence(players, flags)[0] is None
    flags['g1p1']['unverified_buff_scale'] = False
    flags['g1p1']['unverified_occlusion'] = True
    assert _timeline_evidence(players, flags)[0] is None
    colored = [players[0], replace(players[1], color='black', stacks=1), *players[2:]]
    assert _timeline_evidence(colored, flags)[0] == 'active'


def test_late_normal_settlement_clears_automatically_and_survives_unreadable_frames():
    timeline = EncounterTimeline()
    timeline.started_at = 80.
    for seconds, sample in ((158, 'active'), (161, 'active'), (162, None), (163, 'clean'), (164, 'clean')):
        phase = timeline.observe(seconds, sample)
        assert not timeline.can_clear_round
    assert phase.key == 'waiting' and timeline.started_at is None
    for seconds in range(165, 182):
        assert timeline.observe(seconds, None).key == 'waiting'
    assert timeline.observe(182, 'active').key == 'waiting'
    assert timeline.observe(183, 'active').key == 'before_tank'
    assert timeline.started_at == 182


def test_first_clear_frame_hides_the_fallback_even_after_the_deadline():
    timeline = EncounterTimeline()
    timeline.started_at = 0.
    deadline = SETTLEMENT + MANUAL_CLEAR_DELAY
    timeline.observe(deadline, 'active')
    assert timeline.can_clear_round
    assert timeline.observe(deadline + .5, 'clean') == SETTLING
    assert not timeline.can_clear_round
    assert timeline.observe(deadline + 1, 'clean').key == 'waiting'
    assert not timeline.can_clear_round


def test_worker_manual_clear_waits_for_new_red_or_black_without_a_clean_panel():
    snapshot = [replace(p, color='none', stacks=0) for p in _roster()]
    snapshot[0] = replace(snapshot[0], color='black', stacks=2, isolated=True)

    class Vision:
        diagnostics = {p.player_key: {'localized': True} for p in snapshot}

        def analyze(self, image):
            return snapshot

    worker = AnalysisWorker()
    worker._new_engine = lambda *_: Vision()
    results = []
    errors = []
    worker.completed.connect(lambda result, players, data: results.append((result, data)))
    worker.failed.connect(lambda error, data: errors.append(error))
    config = dict(generation=1, layout=TeamLayout().to_dict(), mode='all',
                  tank_key='g1p1', auto_timeline=True, timeline_session=1)

    def process(seconds):
        worker.process(QImage(), {'captured_at': seconds}, config)
        assert not errors
        return results[-1]

    deadline = SETTLEMENT + MANUAL_CLEAR_DELAY
    process(deadline - 2.5)
    worker._timeline.started_at = 0.
    assert not process(deadline - .5)[1]['timeline_can_clear']
    assert process(deadline)[1]['timeline_can_clear']
    config.update(generation=2, timeline_session=2, timeline_clear_from=1)
    result, data = process(deadline + .5)
    assert data['timeline_phase'] == 'waiting' and not data['timeline_can_clear']
    assert result.plan is None and not result.annotations
    snapshot[0] = replace(snapshot[0], stacks=1)
    assert process(deadline + 1.5)[1]['timeline_phase'] == 'waiting'
    assert process(deadline + 6.5)[1]['timeline_phase'] == 'waiting'
    snapshot[1] = replace(snapshot[1], color='red', stacks=1)
    assert process(deadline + 7)[1]['timeline_phase'] == 'waiting'
    _, data = process(deadline + 7.5)
    assert data['timeline_phase'] == 'before_tank' and data['timeline_started_at'] == deadline + 7


def _observed(players, states):
    team = []
    for player in players:
        state = states[player.player_key]
        color = 'red' if state.value > 0 else 'black' if state.value < 0 else 'none'
        team.append(replace(player, color=color, stacks=abs(state.value),
                            alive=state.alive, isolated=state.isolated))
    return team


def test_worker_keeps_one_plan_through_casts_and_rearms_after_settlement():
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
    clean_with_isolation = [replace(clean[0], isolated=True), *clean[1:]]
    settled = [
        replace(p, color='red', stacks=1 + i % 4) if i < 10 else
        replace(p, color='black', stacks=1 + i % 3) if i < 20 else p
        for i, p in enumerate(clean)
    ]
    settled[24] = replace(settled[24], color='red', stacks=5, alive=False)
    expected = {0: 'syncing', .5: 'waiting', 2: 'before_tank', 42.5: 'tank_cast',
                46.5: 'after_tank', 47.5: 'fan_cast', 49.5: 'after_fan', 62: 'circle_1',
                64.5: 'circle_2', 66: 'circle_3', 67: 'settling', 79: 'waiting',
                80.5: 'before_tank'}
    expected_cast_labels = {42.5: '结算一式打T', 47.5: '结算二式扇形', 62: '结算一圈',
                            64.5: '结算二圈', 66: '结算三圈'}
    original = None
    trace = None
    for half_second in range(162):
        seconds = half_second * .5
        players = clean if seconds < 1.5 else clean_with_isolation if 78.5 <= seconds < 80 else active
        if 46 <= seconds < 48.5:
            players = _observed(active, trace.stage_states['after_tank'])
        elif 48.5 <= seconds < 67:
            players = _observed(active, trace.stage_states['after_fan'])
        elif seconds == 67:
            players = settled
        elif seconds == 68:
            players = [replace(settled[0], color='black'), *settled[1:]]
        elif seconds == 69:
            players = [replace(settled[0], color='unknown', stacks=None), *settled[1:]]
        worker.process(QImage(), {'captured_at': seconds}, config)
        assert not errors
        result, metadata = results[-1]
        if seconds in expected:
            assert metadata['timeline_phase'] == expected[seconds]
        if seconds in expected_cast_labels:
            assert metadata['timeline_label'] == expected_cast_labels[seconds]
        if seconds == 2:
            assert result.plan.snapshot_stage == 'before_tank' and not result.plan.current_snapshot
            original = result
            trace = simulate_plan(players, result.plan, trace_stages=True)
            assert trace.stage_states['after_tank']['g1p1'].value == 4
            assert (result.final_red, result.final_black) == (trace.red_count, trace.black_count)
        if original is not None and 2 < seconds < 79:
            assert metadata.get('timeline_plan_held')
            assert result.plan == original.plan and result.annotations == original.annotations
            assert result.instruction == original.instruction
            assert (result.final_red, result.final_black) == (original.final_red, original.final_black)
        if seconds == 46.5:
            assert result.buff_counts['red4'] == 1
        if seconds == 67:
            assert (result.current_red, result.current_black) == (10, 10)
            assert result.buff_counts == {
                'red1': 3, 'red2': 3, 'red3': 2, 'red4': 2,
                'black1': 3, 'black2': 4, 'black3': 3,
            }
            assert '差 0' in result.summary
        if seconds == 68:
            assert (result.current_red, result.current_black) == (9, 11)
            assert '差 -2' in result.summary
        if seconds == 69:
            assert result.reference and result.buff_counts is not None
            assert (result.current_red, result.current_black) == (13, 12)
        if seconds == 79:
            assert result.buff_counts is None and result.current_red is None
            assert not result.annotations
    assert results[-1][1]['timeline_started_at'] == 80.


def test_auto_cast_and_settlement_report_counts_even_when_no_plan_was_built():
    players = _roster()
    diagnostics = {p.player_key: {'localized': True} for p in players}
    worker = AnalysisWorker()
    worker._timeline.started_at = 1.
    for phase in (TANK_CAST, SETTLING):
        result = worker._auto_result(phase, players, diagnostics, {'generation': 1}, {})
        assert (result.current_red, result.current_black) == (13, 12)
        assert result.buff_counts == {'red1': 13, 'black2': 12}
        assert result.plan is None


def test_auto_reassigns_when_a_marked_player_is_confirmed_dead():
    players = _roster()
    worker = AnalysisWorker()
    worker._timeline.started_at = 1.
    config = {'generation': 1, 'prefer_exact': False}
    diagnostics = {p.player_key: {'localized': True} for p in players}
    initial = worker._auto_result(BEFORE_TANK, players, diagnostics, config,
                                  {'difficulty': 'normal', 'captured_at': 2.})
    key = next(action.player_key for action in initial.plan.actions if action.player_key != 'g1p1')
    changed = [replace(p, color='none', stacks=0, alive=False) if p.player_key == key else p for p in players]

    metadata = {'difficulty': 'normal', 'captured_at': 2.5}
    result = worker._auto_result(BEFORE_TANK, changed, diagnostics, config, metadata)
    assert not metadata.get('timeline_plan_held')
    assert result.plan is not None and simulate_plan(changed, result.plan).valid
    assert all(action.player_key != key for action in result.plan.actions)
    assert all(annotation.player_key != key for annotation in result.annotations)


def test_auto_replans_only_after_two_readable_frames_disagree_at_a_checkpoint(monkeypatch):
    players = _roster()
    worker = AnalysisWorker()
    worker._timeline.started_at = 1.
    config = {'generation': 1, 'prefer_exact': False}
    diagnostics = {p.player_key: {'localized': True} for p in players}
    stages = []
    original_evaluate = worker._evaluate

    def evaluate(roster, mode, stage, config, metadata):
        stages.append(stage)
        return original_evaluate(roster, mode, stage, config, metadata)

    monkeypatch.setattr(worker, '_evaluate', evaluate)

    def deliver(phase, seconds, observed):
        metadata = {'difficulty': 'normal', 'captured_at': seconds}
        return worker._auto_result(phase, observed, diagnostics, config, metadata), metadata

    initial, _ = deliver(BEFORE_TANK, 2., players)
    expected = simulate_plan(players, initial.plan, trace_stages=True).stage_states
    after_tank = _observed(players, expected['after_tank'])
    assert deliver(TANK_CAST, 45., after_tank)[1]['timeline_plan_held']
    assert deliver(AFTER_TANK, 46., after_tank)[0].plan == initial.plan

    after_fan = _observed(players, expected['after_fan'])
    mismatch = [replace(after_fan[0], color='red', stacks=1), *after_fan[1:]]
    first, first_meta = deliver(AFTER_FAN, 49., mismatch)
    assert first_meta['timeline_plan_held'] and first.plan == initial.plan
    assert deliver(AFTER_FAN, 49., mismatch)[1]['timeline_plan_held']
    uncertain = [replace(mismatch[0], color='unknown', stacks=None), *mismatch[1:]]
    assert deliver(AFTER_FAN, 49.2, uncertain)[1]['timeline_plan_held']
    assert deliver(AFTER_FAN, 49.4, mismatch)[1]['timeline_plan_held']
    repaired, repaired_meta = deliver(AFTER_FAN, 49.6, mismatch)
    assert 'timeline_plan_held' not in repaired_meta
    assert stages == ['before_tank', 'after_tank']
    assert repaired.plan.mode == 'circle' and repaired.plan.current_snapshot
    assert not any(action.fan for action in repaired.plan.actions)


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
