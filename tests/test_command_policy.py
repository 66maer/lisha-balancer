from dataclasses import replace
from itertools import product

from lisha_overlay.analysis import evaluate_current
from lisha_overlay.command_solver import command_instruction
from lisha_overlay.solver import PlayerAction,SolverPlan,simulate_plan
from test_strategy_policy import roster,best_four_live_outcome


def test_whole_red_one_group_beats_selecting_fewer_individuals():
    players=roster([1]*6+[3]*9+[-1]*8)
    result=evaluate_current(players,'circle')
    assert result.status=='solved' and result.plan.command_style
    simulation=simulate_plan(players,result.plan)
    assert simulation.valid and result.difference<=2
    assert all(simulation.final_states[p.player_key].value==0 for p in players if p.color=='red' and p.stacks==1)
    assert '红一跟1' in result.instruction and '标记' not in result.instruction
    assert all(not a.fan for a in result.plan.actions)


def test_whole_empty_group_can_fill_black_with_one_circle_and_no_fan():
    players=roster([3]*13+[-1]*6)
    result=evaluate_current(players,'circle')
    assert result.status=='solved' and result.plan.command_style
    simulation=simulate_plan(players,result.plan)
    assert simulation.valid and result.difference<=2
    assert '无Buff跟1' in result.instruction and '标记' not in result.instruction
    assert all(simulation.final_states[p.player_key].value==-1 for p in players if p.color=='none')


def test_two_hit_groups_use_one_and_three_and_never_send_the_raid_to_two():
    players=roster([1]*7+[2]*10+[3]*6+[-1],live=24)
    result=evaluate_current(players,'circle')
    assert result.status=='solved' and result.plan.command_style
    simulation=simulate_plan(players,result.plan)
    assert simulation.valid and result.difference<=2
    assert '红一、红二跟1→3' in result.instruction and result.instruction.count('跟')==1
    assert not any(2 in a.circles for a in result.plan.actions if a.player_key not in result.plan.centers)
    assert all(next(a for a in result.plan.actions if a.player_key==key).circles==(i,)
               for i,key in enumerate(result.plan.centers,1))


def test_same_destination_combines_red_one_and_empty_into_one_clause():
    players=roster([1]*6+[-1]*12)
    centers=('g2p2','g2p3','g2p4')
    actions=tuple(PlayerAction(p.player_key,False,(centers.index(p.player_key)+1,) if p.player_key in centers else (1,))
                  for p in players if p.color in ('red','none') or p.player_key in centers)
    plan=SolverPlan('circle','after_tank',actions,centers,current_snapshot=True,command_style=True)
    assert command_instruction(players,plan)=='红一、无Buff跟1；其余避圈'


def test_one_marked_removal_is_better_than_two_whole_group_commands():
    players=roster([1]*9+[-1]*12)
    result=evaluate_current(players)
    fans=[a for a in result.plan.actions if a.fan]
    assert len(fans)==1 and '标记1黑一吃扇' in result.instruction
    assert simulate_plan(players,result.plan).valid
    assert all(p.color!='none' or simulate_plan(players,result.plan).final_states[p.player_key].value==0 for p in players)


def test_simplest_three_guides_need_no_extra_group_instruction():
    result=evaluate_current(roster([1]*15+[-1]*10),'circle')
    assert result.plan.command_style and len(result.plan.actions)==3
    assert result.instruction=='3红一排圈；其余避圈'


def test_complicated_three_circle_fallback_is_labelled_instead_of_claiming_simple_commands():
    players=roster([2]*25)
    result=evaluate_current(players,'circle')
    assert result.status=='solved' and simulate_plan(players,result.plan).valid
    assert not result.plan.command_style and result.reference
    assert result.instruction.startswith('复杂解：')


def test_order_is_stable_and_isolated_people_are_not_added_to_group_commands():
    players=roster([1]*7+[2]*10+[3]*6+[-1],live=24)
    players[0]=replace(players[0],isolated=True)
    first=evaluate_current(players,'circle');second=evaluate_current(list(reversed(players)),'circle')
    assert first.plan==second.plan and first.instruction==second.instruction
    assert simulate_plan(players,first.plan).valid
    assert not any(a.player_key==players[0].player_key and a.player_key not in first.plan.centers for a in first.plan.actions)


def test_group_policy_preserves_solvability_against_independent_four_player_enumeration():
    # The voice priority deliberately changes the personal-action optimum;
    # existence and the independently replayed game rules must still agree.
    for values in product((-2,-1,0,1,2),repeat=4):
        if abs(sum((v>0)-(v<0) for v in values))<=2:continue
        players=roster(list(values),live=4)
        for mode in ('all','circle','fan'):
            possible=best_four_live_outcome(players,mode) is not None
            result=evaluate_current(players,mode)
            assert (result.status=='solved')==possible,(values,mode,result.details)
            if possible:assert simulate_plan(players,result.plan).valid
