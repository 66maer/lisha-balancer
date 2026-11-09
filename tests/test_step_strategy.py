"""Replay actual recipients as well as generated spoken selectors per phase."""
from pathlib import Path

import numpy as np
import pytest

from lisha_overlay.strategy_catalogue import Catalogue
from lisha_overlay.spoken_catalogue import SpokenCatalogue
from lisha_overlay.step_strategy import (
    build_board,
    catalogue_candidate,
    compile_selector,
    mask_of,
    replay,
    roster_from_counts,
    solve_steps,
    validate_roster,
)
from lisha_overlay.solver import BuffState

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def catalogue():
    base = ROOT/'src/lisha_overlay/assets/strategy'
    return SpokenCatalogue(Catalogue(base), base)


def roster(values):
    return validate_roster([dict(key=f'g{i//5+1}p{i%5+1}', value=int(v)) for i, v in enumerate(values)])


def independent_step(value, isolated, color):
    if isolated:
        return value, True
    new = value+(1 if color == 'red' else -1)
    assert abs(new) < 5
    opposed = value*(1 if color == 'red' else -1) < 0
    return new, opposed and abs(new) >= 2


def verify_board(data):
    r, actions, guides = data['roster'], data['actions'], data['guides']
    assert len(guides) == len(set(guides)) == 3
    assert data['extra_named_count'] <= 5 and not data['guides_included']
    values = [(p['value'], False) for p in r]
    assert [s['id'] for s in data['timeline']] == ['initial', 'fan', 'guides', 'circle1', 'circle2', 'circle3', 'settle']
    for stage in data['timeline'][1:]:
        if stage['id'] == 'fan':
            targets = [i for i, a in enumerate(actions) if a//4]
        elif stage['id'].startswith('circle'):
            order = int(stage['id'][-1])
            targets = [guides[order-1]]
            for i, a in enumerate(actions):
                if i in guides:
                    continue
                if order in {0: (), 1: (1,), 2: (1, 3), 3: (1, 2, 3)}[a%4]:
                    targets.append(i)
        else:
            targets = []
        assert sorted(targets) == sorted(stage['affected'])
        for i in targets:
            values[i] = independent_step(*values[i], 'red' if stage['id'] == 'fan' else 'black')
        assert values == [(p['value'], p['isolated']) for p in stage['states']]
        assert stage['counts']['red'] == sum(v > 0 for v, _ in values)
        assert stage['counts']['black'] == sum(v < 0 for v, _ in values)
    assert abs(data['final']['difference']) <= 2
    selectors = [data['fan_selector']]+[c['selector'] for c in data['cohorts']]
    named = set()
    for selector in selectors:
        # Replay the selector program itself. This prevents attractive text
        # from addressing a different set than the numerical simulation.
        actual = (selector['mask'] | mask_of(selector['include'])) & ~mask_of(selector['exclude'])
        assert actual == selector['target_mask'] == mask_of(selector['selected'])
        named.update(set(selector['named'])-set(guides))
    assert named == set(data['extra_indices']) and len(named) <= 5
    for c in data['cohorts']:
        assert c['reference'] in ('initial', 'after_fan')
        assert c['selector']['selected'] == [i for i, a in enumerate(actions) if i not in guides and a%4 == c['hits']]
    # Interpret the visible selector words independently of their stored masks.
    def interpret(selector, states, universe):
        label = selector['label'].split('（')[0].split('，加')[0]
        kind = selector['kind']
        if kind == 'all':
            selected = set(universe)
        elif kind == 'team':
            teams = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5}
            selected = {i for i in universe if r[i]['team'] in {teams[n] for n in label.removesuffix('队').split('、')}}
        elif kind == 'buff':
            labels = {'黑三': -3, '黑二': -2, '黑一': -1, '无Buff': 0, '红一': 1, '红二': 2, '红三': 3}
            if label == '红Buff的': selected = {i for i in universe if states[i]['value'] > 0}
            elif label == '黑Buff的': selected = {i for i in universe if states[i]['value'] < 0}
            elif label == '有Buff的': selected = {i for i in universe if states[i]['value'] != 0}
            elif label.startswith('身上'):
                layer = {'一': 1, '二': 2, '三': 3}[label[2]]
                selected = {i for i in universe if abs(states[i]['value']) == layer}
            else:
                values = {labels[n] for n in label.split('、')}
                selected = {i for i in universe if states[i]['value'] in values}
        elif kind == 'quota':
            selected = set(selector['selected'])
            assert selected <= set(selector['eligible']) <= set(universe)
            assert not set(selector['eligible']) & set(guides)
            assert len({states[i]['value'] for i in selector['eligible']}) == 1
        else:
            selected = set()
        assert mask_of(selected) == selector['mask']
        selected |= set(selector['include'])
        selected -= set(selector['exclude'])
        assert selected == set(selector['selected'])
    interpret(data['fan_selector'], data['timeline'][0]['states'], set(range(25)))
    for cohort in data['cohorts']:
        states = data['timeline'][0 if cohort['reference'] == 'initial' else 1]['states']
        interpret(cohort['selector'], states, set(range(25))-set(guides))


def test_minimum_volunteer_count_can_replace_a_whole_squad(catalogue):
    r = roster([1]*17+[-1]*8)
    first = solve_steps(r, 'circle', catalogue)
    assert first['status'] == 'solved'
    assert len(first['cohorts']) == 1
    assert first['cohorts'][0]['selector']['kind'] == 'quota'
    assert first['extra_named_count'] == 0
    assert first['prompt'] == '三人 [红1] 引导\n4~8人 [红1] 蹭圈'
    assert first['cues'][0]['call'] == '' and first['cues'][0]['rows'] == []
    assert [variant['count'] for variant in first['quota_variants']] == [4, 5, 6, 7, 8]
    assert len(first['cohorts'][0]['selector']['selected']) == 4
    assert all(r[i]['team'] <= 2 for i, action in enumerate(first['actions']) if action)
    for variant in first['quota_variants']:
        alternative = dict(roster=first['roster'], **variant['board'])
        verify_board(alternative)
        targets = alternative['cohorts'][0]['selector']['selected']
        assert len(targets) == variant['count'] and not set(targets) & set(alternative['guides'])
        assert alternative['prompt'] == first['prompt']
    verify_board(first)


def test_reported_black_one_scene_uses_one_remote_helper_and_exposes_one_to_five(catalogue):
    values = [-1,-1,1,-1,1, 1,-1,-1,-1,1, 1,1,1,1,1, 1,1,-1,-1,-1, -1,-1,-1,-1,-1]
    result = solve_steps(roster(values), 'all', catalogue)
    assert result['initial']['difference'] == -3
    assert result['prompt'] == '1~5人 [黑1] 吃扇\n三人 [黑1] 引导'
    helpers = [i for i, action in enumerate(result['actions']) if action and i not in result['guides']]
    assert helpers == [6]
    assert result['actions'][6] == 4
    assert result['guides'] == [0, 1, 3]
    assert result['final']['red'] == 11 and result['final']['black'] == 13
    assert result['cues'][0]['rows'][0]['balanced_count'] == 3
    for variant in result['quota_variants']:
        assert variant['board']['final']['red'] == 11
        assert variant['board']['final']['black'] == 14-variant['count']
        assert all(i < 20 for i, action in enumerate(variant['board']['actions']) if action)
        verify_board(dict(roster=result['roster'], **variant['board']))


def test_fifth_team_is_reserved_when_other_teams_can_solve(catalogue):
    result = solve_steps(roster([-1]*20+[3]*5), 'all', catalogue)
    assert result['status'] == 'solved'
    assert result['actions'][20:] == [0]*5
    verify_board(result)


def test_two_circle_cohort_is_announced_once_before_any_circle():
    r = roster([2]*22+[1, 1, -1])
    candidate = dict(actions=[2]*22+[1]*3, guides=[22, 23, 24], extras=[], source='test')
    board = build_board(r, candidate)
    assert board is not None
    assert board['timeline'][3]['states'][0]['value'] == 1
    assert board['timeline'][5]['states'][0]['value'] == 0
    assert len(board['cues']) == 2
    assert [c['id'] for c in board['cues']] == ['before_fan', 'before_circles']
    assert '蹭双圈' in board['cues'][1]['call']
    assert not any(s['call'] for s in board['timeline'][3:6])
    assert board['cohorts'][0]['reference'] == 'after_fan'


def test_buff_group_is_evaluated_after_fan_and_not_before():
    r = roster([-1]*22+[1]*3)
    # Full fan clears black1; ordinary players then all gain black1. Guides
    # first become red2, and each becomes red1 at its own strike.
    candidate = dict(actions=[5]*25, guides=[22, 23, 24], extras=[], source='test')
    stages = replay(r, candidate)
    assert stages[1]['states'][0]['value'] == 0
    assert stages[3]['states'][0]['value'] == -1
    assert stages[3]['states'][22]['value'] == 1
    assert stages[3]['states'][23]['value'] == 2
    assert stages[4]['states'][23]['value'] == 1
    assert stages[5]['states'][24]['value'] == 1


def test_blocked_later_hits_keep_generated_isolation_in_replay():
    r = roster([3]*22+[-1]*3)
    stages = replay(r, dict(actions=[3]*22+[1]*3, guides=[22, 23, 24]))
    assert stages[3]['states'][0]['value'] == 2 and stages[3]['states'][0]['isolated']
    assert stages[4]['states'][0]['value'] == stages[5]['states'][0]['value'] == 2
    assert not next(e for e in stages[4]['events'] if e['index'] == 0)['applied']


def test_one_layer_group_does_not_silently_mix_red_and_black():
    r = roster([1]*5+[-1]*5+[0]*15)
    states = [BuffState(p['value']) for p in r]
    selector = compile_selector(r, states, (1 << 25)-1, mask_of(range(10)), 0, '大团')
    assert selector['label'] == '一、二队' or selector['label'] == '身上一层的'
    red_only = compile_selector(r, states, (1 << 25)-1, mask_of(range(5)), 0, '大团')
    assert red_only['label'] in ('一队', '红一', '红Buff的')
    assert red_only['target_mask'] != selector['target_mask']


@pytest.mark.parametrize('mode', ['all', 'circle'])
def test_sampled_squad_arrangements_and_catalogue_fallbacks_replay(catalogue, mode):
    rng = np.random.default_rng(1105)
    examples = [[1]*25, [2]*25, [-1]*24+[1], [1]*17+[-1]*8,
                [0]*10+[2]*5+[3]*10, [3]*25]
    examples += [rng.integers(-3, 4, size=25).tolist() for _ in range(12)]
    for values in examples:
        r = roster(values)
        data = solve_steps(r, mode, catalogue)
        if data['status'] == 'impossible':
            assert catalogue_candidate(r, mode, catalogue) is None
            continue
        assert data['status'] == 'solved', (values, mode, data['prompt'])
        if mode == 'circle':
            assert not any(a//4 for a in data['actions'])
        verify_board(data)
        assert not any(word in data['prompt'] for word in ('其余', '别动', '别吃', '站住', 'boss'))
        for cue in data['cues']:
            for row in cue['rows']:
                assert row['selected'] and row['count'] == len(row['selected'])
                assert sum(buff['count'] for buff in row['buffs']) == row['count']


def test_extra_named_budget_excludes_three_guides():
    # Five exceptions are available in addition to three already named guides.
    r = roster([1]*10+[-1]*15)
    universe = (1 << 25)-1
    target = mask_of(range(5, 25))
    selector = compile_selector(r, [BuffState(p['value']) for p in r], universe, target, mask_of(range(8)), '所有人')
    assert selector is not None
    assert len(set(selector['named'])-set(range(5, 8))) <= 5


def test_red_buff_word_also_includes_four_stacks_generated_by_fan():
    from lisha_overlay.step_strategy import selector_options
    r = roster([3]+[0]*24)
    states = [BuffState(4)]+[BuffState(0)]*24
    option = next(s for s in selector_options(r, states, (1 << 25)-1, '大团') if s['label'] == '红Buff的')
    assert option['mask'] == 1


@pytest.mark.parametrize('change', ['duplicate', 'four', 'dead', 'isolated', 'bool', 'same_name'])
def test_scope_and_input_validation(change):
    r = roster_from_counts([0, 0, 10, 5, 10, 0, 0])
    if change == 'duplicate': r[0]['key'] = r[1]['key']
    elif change == 'four': r[0]['value'] = 4
    elif change == 'dead': r[0]['alive'] = False
    elif change == 'isolated': r[0]['isolated'] = True
    elif change == 'bool': r[0]['value'] = True
    else: r[0]['name'] = r[1]['name'] = 'same'
    with pytest.raises(ValueError):
        validate_roster(r)
