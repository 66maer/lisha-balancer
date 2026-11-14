from dataclasses import replace

import pytest

from lisha_overlay.analysis import AnalysisResult,PlayerState,evaluate_current as evaluate
from lisha_overlay.cached_analysis import AnalysisCache
import lisha_overlay.cached_analysis as module


def roster():
    return [PlayerState(f'g{group}p{slot}','none',0) for group in range(1,6) for slot in range(1,6)]


@pytest.mark.parametrize('mode',['all','fan','circle'])
def test_cache_result_matches_current_solver_and_is_independent_of_caller_mutation(mode):
    players=roster();cache=AnalysisCache();expected=evaluate(players,mode)
    first=cache.evaluate(players,mode);assert first==expected and not cache.last_hit
    first.summary='changed';first.warnings.append('frame-only warning');first.annotations.clear()
    second=cache.evaluate(list(players),mode)
    assert cache.last_hit and second==expected and second is not first


@pytest.mark.parametrize('changes',[{'color':'red','stacks':1},{'stacks':1},{'alive':False},{'isolated':True},{'is_tank':True},{'player_key':'g6p1'}])
def test_any_semantic_roster_change_invalidates_cached_plan(monkeypatch,changes):
    calls=[]
    def solve(players,mode,**kwargs):calls.append((tuple(players),mode,kwargs));return AnalysisResult('computed')
    monkeypatch.setattr(module,'evaluate',solve);players=roster();cache=AnalysisCache()
    cache.evaluate(players);cache.evaluate(players);assert len(calls)==1
    players[0]=replace(players[0],**changes);cache.evaluate(players)
    assert len(calls)==2 and not cache.last_hit


def test_mode_stage_reset_and_bounded_single_entry_cache(monkeypatch):
    calls=[]
    monkeypatch.setattr(module,'evaluate',lambda *args,**kwargs:(calls.append(1) or AnalysisResult('computed')))
    players=roster();cache=AnalysisCache();cache.evaluate(players)
    cache.evaluate(players,'circle');cache.evaluate(players,'circle',snapshot_stage='after_tank')
    cache.evaluate(players);assert len(calls)==4  # Earlier signatures are not retained.
    cache.reset();cache.evaluate(players);assert len(calls)==5


def test_difficulty_change_invalidates_the_cached_plan(monkeypatch):
    calls = []
    def solve(players, mode, **kwargs):
        calls.append(kwargs)
        return AnalysisResult('computed')
    monkeypatch.setattr(module, 'evaluate', solve)
    cache = AnalysisCache()
    players = roster()
    cache.evaluate(players)
    cache.evaluate(players, difficulty='heroic')
    assert not cache.last_hit and calls[-1]['difficulty'] == 'heroic'
    cache.evaluate(players, difficulty='heroic')
    assert cache.last_hit and len(calls) == 2
