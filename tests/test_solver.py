from dataclasses import replace
from itertools import combinations, product
import random

import pytest

from lisha_overlay.analysis import PlayerState, evaluate
from lisha_overlay.solver import (
    BuffState, IllegalAction, PlayerAction, SolverPlan, receive_buff,
    simulate_plan, solve,
)


def roster(values, *, dead_after=None, tank=0):
    players = []
    for index in range(25):
        value = values[index] if index < len(values) else 0
        players.append(PlayerState(
            f"g{index // 5 + 1}p{index % 5 + 1}",
            "red" if value > 0 else "black" if value < 0 else "none", abs(value),
            alive=dead_after is None or index < dead_after, is_tank=index == tank,
        ))
    return players


@pytest.mark.parametrize("start,color,amount,final,weak,strong,isolated", [
    (1, "black", 1, 0, 0, 0, False),
    (2, "black", 1, 1, 1, 0, False),
    (3, "black", 1, 2, 0, 1, True),
    (-2, "red", 3, 1, 1, 0, False),
    (-1, "red", 3, 2, 0, 1, True),
    (-3, "red", 3, 0, 0, 0, False),
    (-4, "red", 3, -1, 1, 0, False),
])
def test_simultaneous_buff_cancellation_and_isolation(start, color, amount, final, weak, strong, isolated):
    event = receive_buff(BuffState(start), color, amount)
    assert event.state == BuffState(final, isolated)
    assert (event.weak, event.strong) == (weak, strong)


@pytest.mark.parametrize("state,color,amount", [(4, "red", 1), (-4, "black", 1), (2, "red", 3), (5, "black", 1)])
def test_five_stack_explosion_is_never_a_legal_tool(state, color, amount):
    with pytest.raises(IllegalAction):
        receive_buff(BuffState(state), color, amount)


def test_isolation_blocks_later_buffs_and_red_three_cannot_eat_two_circles():
    isolated = receive_buff(BuffState(3), "black").state
    assert isolated == BuffState(2, True)
    assert not receive_buff(isolated, "black").applied
    players = roster([0, -1, -1, -1, 3], dead_after=5)
    plan = SolverPlan("all", "before_tank", (
        PlayerAction("g1p2", False, (1,)), PlayerAction("g1p3", False, (2,)),
        PlayerAction("g1p4", False, (3,)), PlayerAction("g1p5", False, (1, 2)),
    ), ("g1p2", "g1p3", "g1p4"))
    outcome = simulate_plan(players, plan)
    assert not outcome.valid and "隔绝" in outcome.reason


def test_tank_three_red_is_mandatory_and_never_given_to_other_players():
    players = roster([2] + [-1] * 12 + [1] * 12)
    assert evaluate(players, snapshot_stage="before_tank").status == "no_solution"
    after = evaluate(players, snapshot_stage="after_tank")
    assert after.status == "solved"
    assert not any(mark.player_key == "g1p1" and mark.kind == "fan" for mark in after.annotations)
    assert simulate_plan(players, after.plan).valid


@pytest.mark.parametrize("stage", ["before_tank", "after_tank"])
def test_no_tank_reference_never_adds_unknown_three_red_or_guesses_a_tank(stage):
    players = roster([2] + [-1] * 12 + [1] * 12, tank=None)
    result = evaluate(players, snapshot_stage=stage)
    assert result.status == "solved" and result.plan.reference_without_tank
    verified = simulate_plan(players, result.plan)
    assert verified.valid and verified.difference <= 2
    assert all(abs(state.value) < 5 for state in verified.final_states.values())
    assert "承伤 T 不可按扇标执行" in result.details
    stage_notice = "未计一式 +3 赤煞" if stage == "before_tank" else "一式已结算"
    assert stage_notice in result.details
    # A reference plan cannot be mistaken for a complete tank-aware plan.
    assert not simulate_plan(players, replace(result.plan, reference_without_tank=False)).valid
    assigned = [replace(player, is_tank=index == 0) for index, player in enumerate(players)]
    assert not simulate_plan(assigned, result.plan).valid


def test_no_tank_fan_reference_can_use_the_first_player_and_replays_every_circle():
    players = roster([-1, -1, -1, -2, -2, -2, 1], dead_after=7, tank=None)
    result = evaluate(players, "fan")
    assert result.status == "solved" and result.plan.reference_without_tank
    assert any(action.player_key == "g1p1" and action.fan for action in result.plan.actions)
    circles = [action for action in result.plan.actions if action.circles]
    assert len(circles) == 3 and all(len(action.circles) == 1 for action in circles)
    assert set(action.player_key for action in circles) == set(result.plan.centers)
    assert simulate_plan(players, result.plan).valid


@pytest.mark.parametrize("mode", ["all", "fan", "circle"])
def test_missing_or_dead_tank_reference_is_explicit_and_strict_solver_stays_strict(mode):
    missing = roster([1] * 12 + [-1] * 12, tank=None)
    dead = roster([1] * 12 + [-1] * 12, dead_after=24, tank=24)
    for players, prefix in ((missing, "未选T参考"), (dead, "T重伤参考")):
        assert solve(players, mode).plan is None
        result = evaluate(players, mode)
        assert result.status == "solved" and result.summary.startswith(prefix)
        assert result.plan.reference_without_tank
        assert simulate_plan(players, result.plan).valid
        if players[-1].is_tank:
            assert all(action.player_key != players[-1].player_key for action in result.plan.actions)
        if mode == "circle":
            assert not any(action.fan for action in result.plan.actions)


def test_no_tank_modes_are_separate_and_failed_reference_is_not_calibration_failure():
    players = roster([1] * 15 + [-1] * 10, tank=None)
    circles = evaluate(players, "circle")
    fan = evaluate(players, "fan")
    assert circles.status == "solved" and circles.plan.reference_without_tank
    assert not any(action.fan for action in circles.plan.actions)
    assert fan.status == "no_solution" and fan.summary.startswith("未选T参考")
    assert "按当前 Buff 参考" in fan.details and "未计一式 +3 赤煞" in fan.details
    assert fan.annotations == []


def test_reference_flag_never_permits_multiple_tanks_even_if_one_is_dead():
    players = roster([1] * 12 + [-1] * 12, tank=None)
    found = solve(players, allow_missing_tank=True)
    assert found.plan is not None
    players[0] = replace(players[0], is_tank=True)
    players[-1] = replace(players[-1], is_tank=True, alive=False)
    assert solve(players, allow_missing_tank=True).plan is None
    assert not simulate_plan(players, found.plan).valid


@pytest.mark.parametrize("mode", ["all", "fan", "circle"])
def test_two_black_bombs_do_not_get_an_unreachable_tank_explosion_rescue(mode):
    players = roster([-2] * 25)
    result = evaluate(players, mode)
    assert result.status == "no_solution" and result.summary == "当前站位无解"
    assert result.annotations == []


def test_two_red_bomb_pattern_can_use_continuous_first_and_second_circle_helpers():
    players = roster([0, 0, 0] + [2] * 22)
    result = evaluate(players, "circle")
    assert result.status == "solved" and result.difference <= 2
    assert any(len(action.circles) >= 2 for action in result.plan.actions)
    assert not any(action.fan for action in result.plan.actions)
    assert simulate_plan(players, result.plan).valid
    # A traditional two-circle arrangement remains legal even if the search
    # prefers fewer helpers using a three-circle intersection.
    centers = ("g1p2", "g1p3", "g1p4")
    actions = tuple(PlayerAction(key, False, (order,)) for order, key in enumerate(centers, 1))
    actions += tuple(PlayerAction(player.player_key, False, (1, 2)) for player in players[4:])
    assert simulate_plan(players, SolverPlan("circle", "before_tank", actions, centers)).valid


def test_six_survivor_red_two_pattern_needs_three_circle_helpers():
    players = roster([0, 2, 2, 2, 2, 2], dead_after=6)
    result = evaluate(players, "circle")
    assert result.status == "solved" and result.difference == 0
    assert result.final_red == 3 and result.final_black == 3
    assert sum(action.circles == (1, 2, 3) for action in result.plan.actions) == 3
    assert "三圈重叠" in result.details
    assert simulate_plan(players, result.plan).valid


def test_circle_only_can_remove_red_majority_but_fan_only_cannot():
    players = roster([1] * 15 + [-1] * 10)
    circles = evaluate(players, "circle", snapshot_stage="after_tank")
    assert circles.status == "solved" and circles.difference <= 1
    assert {mark.kind for mark in circles.annotations} == {"circle"}
    assert evaluate(players, "fan", snapshot_stage="after_tank").status == "no_solution"


def test_fan_only_still_assigns_three_neutral_black_centers():
    players = roster([3] + [-1] * 14 + [1] * 10)
    result = evaluate(players, "fan", snapshot_stage="after_tank")
    assert result.status == "solved" and result.difference <= 1
    centers = [mark for mark in result.annotations if mark.kind == "circle"]
    assert len(centers) == 3 and {mark.order for mark in centers} == {1, 2, 3}
    assert all(mark.role == "center" for mark in centers)
    assert any(mark.kind == "fan" for mark in result.annotations)
    assert simulate_plan(players, result.plan).valid


def test_fan_mode_cannot_pretend_mandatory_circles_do_not_exist():
    assert evaluate(roster([0] * 25), "fan").status == "no_solution"


def test_dead_players_get_no_actions_and_isolated_players_are_neutral_centers_only():
    players = roster([0, -1, -1, -1, 1], dead_after=5)
    players[4] = replace(players[4], isolated=True)
    result = evaluate(players)
    assert result.status == "solved"
    assert all(mark.player_key != "g2p1" for mark in result.annotations)
    assert all(mark.kind == "circle" and mark.role == "center"
               for mark in result.annotations if mark.player_key == "g1p5")
    assert simulate_plan(players, result.plan).valid


@pytest.mark.parametrize("mode", ["all", "fan", "circle"])
def test_isolated_targets_are_legal_neutral_centers_and_block_tank_three_red(mode):
    players = [replace(player, isolated=True) for player in roster([1] * 12 + [-1] * 12 + [0])]
    result = evaluate(players, mode)
    assert result.status == "solved" and result.difference == 0
    assert result.final_red == 12 and result.final_black == 12
    assert len(result.plan.actions) == 3
    assert all(action.circles and len(action.circles) == 1 and not action.fan for action in result.plan.actions)
    assert all(mark.role == "center" for mark in result.annotations)
    assert simulate_plan(players, result.plan).valid


def test_isolated_player_is_not_an_effective_circle_helper():
    players = roster([0, -1, -1, -1, 1], dead_after=5)
    players[4] = replace(players[4], isolated=True)
    plan = SolverPlan("all", "before_tank", (
        PlayerAction("g1p2", False, (1,)), PlayerAction("g1p3", False, (2,)),
        PlayerAction("g1p4", False, (3,)), PlayerAction("g1p5", False, (1,)),
    ), ("g1p2", "g1p3", "g1p4"))
    assert not simulate_plan(players, plan).valid


def test_duplicate_centers_and_tank_fan_fail_the_independent_replay():
    players = roster([0, -1, -1, -1], dead_after=4)
    invalid = SolverPlan("all", "before_tank", (
        PlayerAction("g1p1", True),
        PlayerAction("g1p2", False, (1,)), PlayerAction("g1p3", False, (2,)),
        PlayerAction("g1p4", False, (3,)),
    ), ("g1p2", "g1p2", "g1p4"))
    assert not simulate_plan(players, invalid).valid


def brute_force_four_live(players, mode):
    """Enumerate independently of the DP and its per-person option pruning."""
    keys = [player.player_key for player in players[:4]]
    for center_keys in combinations(keys, 3):
        order = {key: index + 1 for index, key in enumerate(center_keys)}
        for fans in product((False, True), repeat=3):
            if mode == "circle" and any(fans):
                continue
            fan_by_key = dict(zip(keys[1:], fans))
            for helper_hits in (0, 1, 2, 3):
                actions = []
                for key in keys:
                    circles = ((order[key],) if key in order else
                               tuple(range(1, helper_hits + 1)) if helper_hits else ())
                    if circles or fan_by_key.get(key):
                        actions.append(PlayerAction(key, fan_by_key.get(key, False), circles))
                plan = SolverPlan(mode, "before_tank", tuple(actions), center_keys)
                if simulate_plan(players, plan).valid:
                    return True
    return False


@pytest.mark.parametrize("mode", ["all", "fan", "circle"])
def test_dp_existence_agrees_with_independent_small_roster_enumeration(mode):
    sample = random.Random(612)
    cases = [[sample.choice((-3, -2, -1, 0, 1, 2, 3)) for _ in range(4)] for _ in range(35)]
    for index, values in enumerate(cases):
        players = roster(values, dead_after=4)
        if index % 3 == 0:
            players[index % 4] = replace(players[index % 4], isolated=True)
        found = solve(players, mode)
        assert (found.plan is not None) == brute_force_four_live(players, mode), (values, mode)
        if found.plan is not None:
            assert simulate_plan(players, found.plan).valid


def test_each_full_roster_result_replays_and_never_hides_illegal_mode_actions():
    sample = random.Random(9021)
    for _ in range(30):
        players = roster([sample.choice((-3, -2, -1, 0, 1, 2, 3)) for _ in range(25)])
        for mode in ("all", "fan", "circle"):
            result = evaluate(players, mode)
            assert result.status in ("solved", "no_solution")
            if result.status == "solved":
                verified = simulate_plan(players, result.plan)
                assert verified.valid and verified.difference <= 2
                assert all(abs(state.value) < 5 for state in verified.final_states.values())
                if mode == "circle":
                    assert not any(mark.kind == "fan" for mark in result.annotations)
