from dataclasses import replace
from types import SimpleNamespace

import numpy as np
from PySide6.QtGui import QImage

from lisha_overlay.analysis import PlayerState
from lisha_overlay.live_strategy import evaluate_exact_commands, evaluate_simple_commands
from lisha_overlay.solver import PlayerAction, SolverPlan, simulate_plan
from lisha_overlay.spoken_planner import CallPlanner, buff_instruction
from lisha_overlay.xinfa import catalogue
from lisha_overlay.xinfa_recognition import RosterScan


def roster(values, hearts):
    return [PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if value > 0 else 'black' if value else 'none',
                        abs(value), xinfa=hearts.get(i, 'aoxue')) for i, value in enumerate(values)]


def test_simple_accepts_safe_difference_for_easier_guides_and_exact_reaches_zero():
    players = roster([1] * 13 + [-2] * 12, {13: 'binxin', 15: 'binxin', 22: 'lijing'})
    simple = evaluate_simple_commands(players, 'circle', difficulty='heroic')
    exact = evaluate_exact_commands(players, 'circle', difficulty='heroic')
    assert (simple.difference, exact.difference) == (1, 0)
    assert simple.strategy['effort'] < exact.strategy['effort']
    assert set(simple.plan.centers) == {'g3p4', 'g4p1', 'g5p3'}
    assert simple.instruction == '\n3队和4队冰心、奶花引导\n'
    assert all(simulate_plan(players, result.plan, tolerance=1).valid for result in (simple, exact))


def test_team_school_phrase_covers_exactly_the_two_assigned_tangmen():
    players = roster([0] * 25, {0: 'jingyu', 5: 'tianluo', 10: 'jingyu'})
    plan = SolverPlan('all', 'after_tank', (PlayerAction('g1p1', True), PlayerAction('g2p1', True)),
                      ('g4p1', 'g4p2', 'g4p3'), current_snapshot=True)
    text, groups, _ = CallPlanner(players).compile(plan)
    assert text.splitlines()[0] == '1队和2队唐门吃扇'
    assert groups[0]['keys'] == ['g1p1', 'g2p1']


def test_unknown_heart_prevents_calling_an_entire_unconfirmed_heart_group():
    players = roster([0] * 25, {0: 'binxin', 1: 'binxin', 2: None})
    plan = SolverPlan('all', 'after_tank', (PlayerAction('g1p1', True), PlayerAction('g1p2', True)),
                      ('g4p1', 'g4p2', 'g4p3'), current_snapshot=True)
    text, groups, _ = CallPlanner(players).compile(plan)
    assert text.splitlines()[0] == '1队1、2号吃扇'
    assert groups[0]['keys'] == ['g1p1', 'g1p2']
    assert catalogue()['youluo'].nickname == '无相楼'


def test_unique_heart_needs_no_number_and_unselected_duplicates_stay_excluded():
    players = roster([-1] + [0] * 24, {0: 'youluo', 1: 'binxin', 2: 'binxin', 5: 'binxin'})
    planner = CallPlanner(players)
    for key, expected in (('g1p1', '无相楼吃扇'), ('g1p2', '1队2号吃扇')):
        plan = SolverPlan('all', 'after_tank', (PlayerAction(key, True),),
                          ('g4p1', 'g4p2', 'g4p3'), current_snapshot=True)
        text, groups, _ = planner.compile(plan)
        assert text.splitlines()[0] == expected
        assert groups[0]['keys'] == [key]


def test_buff_plan_counts_exact_recipients_at_the_time_of_the_action():
    players = roster([-1, 2, 1, -1, -1, 1, 1, 1] + [0] * 17, {})
    plan = SolverPlan(
        'all', 'after_tank', (
            PlayerAction('g1p1', True, (1,)),
            PlayerAction('g1p2', False, (1, 3)),
            PlayerAction('g1p3', False, (1,)),
            PlayerAction('g1p4', False, (2,)),
            PlayerAction('g1p5', False, (3,)),
        ), ('g1p3', 'g1p4', 'g1p5'), current_snapshot=True, spoken_policy=True,
    )
    assert simulate_plan(players, plan).valid
    assert buff_instruction(players, plan).splitlines() == [
        '{黑1}×1 吃扇', '{红1}×1 {黑1}×2 引导', '无煞×1 蹭圈，{红2}×1 蹭2圈',
    ]


def test_healer_and_ranged_are_preferred_over_melee_among_equivalent_guides():
    players = roster([1] * 11 + [-1] * 11 + [0] * 3,
                     {16: 'youluo', 20: 'lingsu', 21: 'zixia'})
    result = evaluate_simple_commands(players, 'circle')
    assert simulate_plan(players, result.plan).valid
    assert set(result.plan.centers) == {'g4p2', 'g5p1', 'g5p2'}
    assert '无相楼' in result.instruction
    changed = [replace(p, xinfa=None) for p in players]
    assert evaluate_simple_commands(changed, 'circle').balanced


def test_roster_draft_can_be_delivered_again_without_sampling_after_confirmation():
    calls = []
    probabilities = {p.player_key: np.array([.95, .05]) for p in roster([0] * 25, {})}

    def read(*_args):
        calls.append(1)
        return probabilities, []

    reader = SimpleNamespace(classes=('binxin', 'aoxue'), read=read)
    scan, image = RosterScan(), QImage(12, 12, QImage.Format.Format_RGB32)
    assert scan.observe(reader, image, None, 1) is None
    assert scan.observe(reader, image, None, 1.1) is None
    assert scan.observe(reader, image, None, 1.5) is None
    draft = scan.observe(reader, image, None, 2)
    assert len(draft) == 25 and set(draft.values()) == {'binxin'}
    assert scan.observe(reader, image, None, 3) == draft and len(calls) == 3
