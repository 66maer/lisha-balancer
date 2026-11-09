from dataclasses import replace
from itertools import combinations,product
import random

from lisha_overlay.analysis import PlayerState,evaluate_current
from lisha_overlay.solver import PlayerAction,SolverPlan,simulate_plan,solve


def roster(values,live=25):
    return [PlayerState(f'g{i//5+1}p{i%5+1}',
        'red' if value>0 else 'black' if value<0 else 'none',abs(value),alive=i<live)
        for i,value in enumerate((values+[0]*25)[:25])]


def test_balanced_roster_needs_no_correction_marks_or_optional_fan():
    players=roster([1]*12+[-1]*12)
    result=evaluate_current(players)
    verified=simulate_plan(players,result.plan)
    assert result.status=='solved' and verified.valid and result.balanced
    assert result.difference==0 and result.final_red==result.final_black==12
    assert result.annotations==[] and not any(a.fan for a in result.plan.actions)
    assert result.plan.centers==('g3p3','g3p4','g3p5')
    assert result.instruction=='黑一/黑二引导黑圈'


def test_current_strategy_ignores_old_tank_assignments_and_stage():
    players=roster([2]+[-1]*12+[1]*12)
    expected=evaluate_current(players)
    flagged=[replace(p,is_tank=i in (0,1,24)) for i,p in enumerate(players)]
    result=evaluate_current(flagged,snapshot_stage='before_tank')
    assert result==expected and result.plan.current_snapshot
    assert simulate_plan(flagged,result.plan).valid
    assert 'T' not in result.summary and 'T' not in result.instruction


def test_counts_include_all_layers_and_difference_is_red_minus_black():
    players=roster([1,2,3,4,1,2]+[-1,-2,-3,-4]*2)
    players[0]=replace(players[0],isolated=True)
    result=evaluate_current(players)
    assert result.current_red==6 and result.current_black==8
    assert result.summary=='红 6  黑 8  差 -2  平衡'


def test_first_column_first_and_circle_numbers_increase_in_panel_order():
    players=[replace(p,isolated=True) for p in roster([1]*12+[-1]*12)]
    result=evaluate_current(players)
    assert result.plan.centers==('g3p3','g3p4','g3p5')
    assert evaluate_current(list(reversed(players))).plan==result.plan
    assert result.annotations==[]
    # When correction is necessary, start at column one, top to bottom.
    unbalanced=roster([1]*15+[-1]*10)
    corrected=evaluate_current(unbalanced,'circle')
    assert corrected.plan.centers==('g1p1','g1p2','g1p3')
    assert [a.player_key for a in corrected.plan.actions]==['g1p1','g1p2','g1p3']


def best_four_live_outcome(players,mode):
    """Enumerate legal instructions without the DP or its option dominance."""
    keys=[p.player_key for p in players[:4]]
    best=None
    for centers in combinations(keys,3):
        order={key:i+1 for i,key in enumerate(centers)}
        for fans in product((False,True),repeat=4):
            if mode=='circle' and any(fans):continue
            for hits in range(4):
                actions=[]
                for key,fan in zip(keys,fans):
                    circles=(order[key],) if key in order else tuple(range(1,hits+1))
                    if circles or fan:actions.append(PlayerAction(key,fan,circles))
                plan=SolverPlan(mode,'after_tank',tuple(actions),centers,current_snapshot=True)
                sim=simulate_plan(players,plan)
                if sim.valid:
                    created=sum(p.alive and p.color=='none' and sim.final_states[p.player_key].value!=0 for p in players)
                    score=(sim.strong_cancellations,created,sum(a.fan+bool(a.circles) for a in actions),
                        sum(max(0,len(a.circles)-1) for a in actions),
                        sum(a.fan+len(a.circles) for a in actions),sim.difference)
                    if best is None or score<best:best=score
    return best


def test_individual_fallback_matches_independent_exhaustive_search():
    sample=random.Random(485)
    cases=[[-1,-1,-1,0],[1,1,1,0],[-2,-1,-1,-3],[1,1,1,1],[2,2,2,2],[-2,-2,-2,0]]
    cases += [[sample.choice((-3,-2,-1,0,1,2,3)) for _ in range(4)] for _ in range(16)]
    for index,values in enumerate(cases):
        players=roster(values,live=4)
        if index%3==0:players[0]=replace(players[0],isolated=True)
        delta=sum(p.alive and p.color=='red' for p in players)-sum(p.alive and p.color=='black' for p in players)
        if abs(delta)<=2:continue
        for mode in ('all','fan','circle'):
            best=best_four_live_outcome(players,mode)
            result=solve(players,mode,current_snapshot=True)
            assert (result.plan is not None)==(best is not None)
            if best is not None:
                sim=simulate_plan(players,result.plan)
                assert sim.valid
                actions=result.plan.actions
                created=sum(p.alive and p.color=='none' and sim.final_states[p.player_key].value!=0 for p in players)
                assert (sim.strong_cancellations,created,sum(a.fan+bool(a.circles) for a in actions),
                    sum(max(0,len(a.circles)-1) for a in actions),
                    sum(a.fan+len(a.circles) for a in actions),sim.difference)==best


def test_safe_fallback_keeps_one_or_two_player_tolerance():
    for majority in (1,2):
        players=[replace(p,isolated=True) for p in roster([1]*(10+majority)+[-1]*10)]
        result=evaluate_current(players)
        assert result.status=='solved' and result.difference==majority and result.balanced
        assert result.annotations==[]
    players=[replace(p,isolated=True) for p in roster([1]*13+[-1]*10)]
    assert evaluate_current(players).status=='no_solution'


def test_one_fan_is_enough_and_removal_wins_over_creating_red():
    players=roster([1]*9+[-1]*12)
    result=evaluate_current(players)
    assert not result.balanced and result.status=='solved' and result.difference==2
    fans=[a for a in result.plan.actions if a.fan]
    assert len(fans)==1
    assert next(p for p in players if p.player_key==fans[0].player_key).color=='black'
    assert len(result.plan.actions)==4
    assert result.final_red==9 and result.final_black==11


def test_missing_black_one_two_guides_does_not_invent_optional_mechanics():
    result=evaluate_current(roster([3]*10+[-3]*10))
    assert result.balanced and result.annotations==[] and result.plan is None
    assert result.final_red==result.final_black==10
    assert '不足3人' in result.instruction and result.warnings


def test_unknown_or_five_stack_frame_cannot_claim_balanced():
    players=roster([1]*12+[-1]*12)
    assert not evaluate_current([replace(players[0],color='unknown',stacks=None),*players[1:]]).balanced
    assert not evaluate_current([replace(players[0],stacks=5),*players[1:]]).balanced
