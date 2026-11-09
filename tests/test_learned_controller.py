from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from lisha_overlay.analysis import AnalysisResult,PlayerState
from lisha_overlay.app import OverlayController


def controller():
    c=SimpleNamespace(_processing_busy=False,_quitting=False,test_enabled=False,_generation=4,
        _analysis_attempts=0,_continuous_analysis=True,_analysis_pending=False,_stable_frames=0,
        _recognition_previous=None,_last_diagnostic_signature=None,_last_result=None,
        isolation_confirmed=False,manual_overrides={},scan=SimpleNamespace(set_annotations=Mock()),
        panel=SimpleNamespace(set_summary=Mock(),set_status=Mock()),apply_analysis=Mock(),_record_analysis=Mock())
    c._complete_learned_snapshot=lambda *args:OverlayController._complete_learned_snapshot(c,*args)
    return c


def roster():
    return [PlayerState(f'g{i//5+1}p{i%5+1}','none',0) for i in range(25)]


def deliver(c,players,t,summary='A',status='solved',balanced=False):
    metadata={'generation':4,'captured_at':t,'vision_backend':'learned',
        'diagnostics':{p.player_key:{'localized':True,'unverified_isolation':p.isolated is None} for p in players}}
    OverlayController._analysis_completed(c,AnalysisResult(summary,status=status,balanced=balanced),players,metadata)
    return metadata


def test_controller_publishes_current_complete_frame_immediately_as_reference():
    c=controller();players=roster();deliver(c,players,0.);deliver(c,players,.1)
    assert c.apply_analysis.call_count==2
    assert not c.apply_analysis.call_args.args[0].reference
    changed=[replace(players[0],color='red',stacks=1),*players[1:]]
    metadata=deliver(c,changed,.2,'B')
    assert metadata['confirmation_status']=='confirming' and metadata['published']
    assert c.apply_analysis.call_args.args[0].summary=='B'
    assert c.apply_analysis.call_args.args[0].reference
    assert c._players[0].color=='red'
    assert metadata['observed_players'][0]['color']=='red'
    deliver(c,changed,.3,'B')
    assert c.apply_analysis.call_args.args[0].summary=='B'
    assert c._players[0].color=='red'


def test_an_unverified_frame_clears_the_display_and_cannot_reuse_old_confirmation():
    c=controller();players=roster();deliver(c,players,0.);deliver(c,players,.1)
    hidden=[replace(players[0],isolated=None),*players[1:]]
    metadata=deliver(c,hidden,.2,'隔绝待确认',status='needs_recognition')
    assert not metadata['published']
    c.scan.set_annotations.assert_called_with([])
    assert deliver(c,players,.3)['confirmation_status']=='confirming'
    assert deliver(c,players,.4)['confirmation_status']=='confirmed'


def test_late_generation_and_duplicate_capture_never_promote_reference_to_confirmed():
    c=controller();players=roster();deliver(c,players,0.);deliver(c,players,0.)
    assert c.apply_analysis.call_count==2
    assert c.apply_analysis.call_args.args[0].reference
    OverlayController._analysis_completed(c,AnalysisResult('stale',status='solved'),players,
        {'generation':3,'captured_at':.1,'vision_backend':'learned'})
    assert c.apply_analysis.call_count==2


@pytest.mark.parametrize('before,after',[(False,True),(True,False)])
def test_crossing_balance_threshold_publishes_current_state_without_old_marks(before,after):
    c=controller();players=roster();deliver(c,players,0.,balanced=before);deliver(c,players,.1,balanced=before)
    assert c.apply_analysis.call_count==2
    changed=[replace(players[0],color='red',stacks=1),*players[1:]]
    metadata=deliver(c,changed,.2,'新状态',balanced=after)
    assert metadata['confirmation_status']=='confirming' and metadata['published']
    assert c.apply_analysis.call_args.args[0].summary=='新状态'
    assert c.apply_analysis.call_args.args[0].balanced==after
    assert c.apply_analysis.call_args.args[0].reference
    metadata=deliver(c,changed,.3,'新状态',balanced=after)
    assert metadata['confirmation_status']=='confirmed' and metadata['published']
