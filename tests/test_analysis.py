from dataclasses import replace

import pytest

from lisha_overlay.analysis import PlayerState, demo_result, evaluate


def roster():
    players = [PlayerState(f"g{group}p{slot}", "none", 0)
               for group in range(1, 6) for slot in range(1, 6)]
    players[0] = replace(players[0], is_tank=True)
    players[1] = replace(players[1], color="red", stacks=1)
    players[2] = replace(players[2], color="black", stacks=1)
    players[3] = replace(players[3], color="red", stacks=2)
    return players


@pytest.mark.parametrize("mode,kinds", [("all", {"fan", "circle"}), ("fan", {"fan"}), ("circle", {"circle"})])
def test_demo_modes_are_explicit_fixtures_and_circle_digits_are_order(mode, kinds):
    result = demo_result(mode)
    assert result.status == "demo" and result.summary.startswith("测试")
    assert {item.kind for item in result.annotations} == kinds
    if mode != "fan":
        assert sorted(item.order for item in result.annotations if item.kind == "circle") == [1, 2, 3]
    assert len({item.player_key for item in result.annotations}) == len(result.annotations)


@pytest.mark.parametrize("change", [dict(color="unknown", stacks=None), dict(stacks=None), dict(stacks=True), dict(stacks=0)])
def test_incomplete_recognition_never_creates_instructions_or_claims_no_solution(change):
    players = roster()
    players[1] = replace(players[1], **change)
    result = evaluate(players)
    assert result.status == "needs_recognition"
    assert result.summary == "待识别" and result.annotations == []


def test_missing_or_duplicate_cells_are_calibration_failure():
    assert evaluate(roster()[:24]).status == "needs_calibration"
    players = roster()
    players[2] = replace(players[2], player_key=players[1].player_key)
    assert evaluate(players).status == "needs_calibration"
    players = roster()
    players[2] = replace(players[2], player_key="unmapped-player")
    assert evaluate(players).status == "needs_calibration"


def test_missing_tank_uses_current_buff_reference_without_guessing_a_player():
    players = roster()
    players[0] = replace(players[0], is_tank=False)
    result = evaluate(players)
    assert result.status == "solved" and result.plan.reference_without_tank
    assert result.summary.startswith("未选T参考 · ")
    assert "未计一式 +3 赤煞" in result.details
    assert "承伤 T 不可按扇标执行" in result.details
    assert not any(player.is_tank for player in players)


def test_multiple_tanks_are_still_rejected_including_a_dead_second_tank():
    players = roster()
    players[1] = replace(players[1], is_tank=True, alive=False)
    result = evaluate(players)
    assert result.status == "needs_calibration" and not result.annotations
    assert "多名" in result.details


def test_known_roster_is_solved_by_distinct_mode_searches():
    for mode in ("all", "circle"):
        result = evaluate(roster(), mode)
        assert result.summary == "红1 1 · 黑1 1"
        assert result.status == "solved" and result.plan is not None
        assert result.difference <= 2
    # Only one black carrier exists. Fan-only correction cannot silently use
    # zero/red players to create three new black holders through its circles.
    assert evaluate(roster(), "fan").status == "no_solution"


def test_dead_player_does_not_count_and_isolation_does_not_erase_a_buff():
    players = roster()
    players[1] = replace(players[1], isolated=True)
    players[2] = replace(players[2], alive=False, color="unknown", stacks=None)
    assert evaluate(players).summary == "红1 1 · 黑1 0"


@pytest.mark.parametrize("function", [evaluate, demo_result])
def test_unknown_mode_is_a_programming_error(function):
    with pytest.raises(ValueError):
        function([], "other") if function is evaluate else function("other")
def test_dead_players_unknown_buff_and_isolation_do_not_block_living_roster():
    from dataclasses import replace
    players=[PlayerState(f'g{i//5+1}p{i%5+1}','red' if i<12 else 'black' if i<24 else 'none',1 if i<24 else 0)
             for i in range(25)]
    players[0]=replace(players[0],alive=False,color='unknown',stacks=None,isolated=None)
    result=evaluate(players,'all')
    assert result.status in ('solved','no_solution')
    assert not any(a.player_key==players[0].player_key for a in result.annotations)
