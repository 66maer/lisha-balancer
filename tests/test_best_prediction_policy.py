"""Model choices survive advisory checks, but missing geometry stays invalid."""
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtGui import QImage

from lisha_overlay.analysis import AnalysisResult, PlayerState
from lisha_overlay.learned_recognition import LearnedRecognizer
from lisha_overlay.processing import AnalysisWorker
from lisha_overlay.team_layout import TeamLayout
from lisha_overlay.vision import VisionEngine


@pytest.mark.parametrize('kind,expected,isolated', [(0,'none',False),(3,'none',True)])
def test_non_target_argmax_is_not_forced_to_a_target_and_isolation_stays_boolean(kind,expected,isolated):
    recognizer=LearnedRecognizer()
    class Session:
        def run(self,names,feeds):
            n=len(feeds['icons'])
            scores=np.full((n,4),-10,np.float32);scores[:,kind]=10
            return [scores,np.tile([10,-10,-10,-10],(n,1)).astype(np.float32),np.zeros((n,1),np.float32)]
    recognizer.session=Session()
    image=np.random.default_rng(6).integers(0,256,(38,128,3),dtype=np.uint8)
    row=recognizer.predict([image])[0]
    assert row['state']==expected and row['isolated'] is isolated
    assert row['unverified_isolation']==isolated


def test_no_scale_majority_can_select_none_when_its_model_score_is_largest():
    recognizer=LearnedRecognizer(scale_offsets=(-1,0,1))
    rows=[dict(state=s,isolated=iso,isolation_confirmed=True,confidence=score,icon_side=24+i)
          for i,(s,iso,score) in enumerate((('red_2',False,.6),('black_1',False,.7),('none',False,.95)))]
    result=recognizer._combine_scales(rows,1)[0]
    assert result['state']=='none' and result['confidence']==.95 and result['unverified_scale']


@pytest.mark.parametrize('menu', [False,True])
def test_cursor_and_menu_evidence_do_not_erase_an_available_model_prediction(monkeypatch,menu):
    import lisha_overlay.learned_rois as rois
    import lisha_overlay.learned_geometry as geometry
    monkeypatch.setattr(rois,'alignment_evidence',lambda image,cells:{c.key:True for c in cells})
    monkeypatch.setattr(geometry,'calibrated_bands',lambda image,cells,**kwargs:
        ([(np.ones((29,115,3),np.uint8),(0,0,115,29)) for c in cells],24,0,
         {'left_support':{},'row_keys':[]}))
    engine=VisionEngine(TeamLayout(),backend='learned')
    engine._learned_recognizer=SimpleNamespace(last_inference={'calls':1},predict=lambda crops:[
        dict(state='red_2',isolated=False,isolation_confirmed=True,confidence=.58458,
             unverified_body=False,unverified_scale=False) for crop in crops])
    engine._cursor_occlusion=SimpleNamespace(find=lambda *args:[[80,0,18,27]])
    engine._scene_words=SimpleNamespace(find=lambda *args:[{'bounds':[80,0,98,27]}] if menu else [])
    engine._dead_player=lambda *args:(False,0.)
    image=QImage(650,500,QImage.Format.Format_RGB32);image.fill(0xff202020)
    players=engine.analyze(image)
    assert len(players)==25 and all((p.color,p.stacks,p.isolated)==('red',2,False) for p in players)
    assert all(d['estimated'] and d['unverified_occlusion'] for d in engine.diagnostics.values())
    assert all(d['unverified_scene']==menu for d in engine.diagnostics.values())


def test_worker_passes_candidates_to_solver_and_logs_reference_reasons(monkeypatch):
    players=[PlayerState(f'g{i//5+1}p{i%5+1}', 'red' if i<16 else 'black',1) for i in range(25)]
    diagnostics={p.player_key:{'localized':True,'isolation_confirmed':True} for p in players}
    diagnostics['g5p4'].update(estimated=True,estimate_reasons=['鼠标可能遮挡'],confidence=.58)
    engine=SimpleNamespace(backend='learned',analyze=lambda image:players,diagnostics=diagnostics)
    worker=AnalysisWorker();monkeypatch.setattr(worker,'_new_engine',lambda *args:engine)
    outputs=[];errors=[]
    worker.completed.connect(lambda *args:outputs.append(args));worker.failed.connect(lambda *args:errors.append(args))
    worker.process(QImage(),{},dict(generation=1,layout=TeamLayout().to_dict(),mode='all'))
    assert not errors and len(outputs)==1
    result,actual,metadata=outputs[0]
    assert result.status=='solved' and result.reference and result.annotations
    assert actual==players and metadata['unknown_keys']==[]
    assert metadata['reference_keys']==['g5p4'] and '5队4' in result.details
    assert metadata['model_output_policy']=='best_current_prediction'


def test_advisory_isolation_does_not_block_controller_or_lose_reference_label():
    from test_learned_controller import controller,roster
    from lisha_overlay.app import OverlayController
    c=controller();players=roster()
    metadata=dict(generation=4,captured_at=0.,vision_backend='learned',reference_keys=['g5p4'],
                  diagnostics={p.player_key:dict(localized=True,unverified_isolation=True) for p in players})
    result=AnalysisResult('候选方案',status='solved',reference=True)
    OverlayController._analysis_completed(c,result,players,metadata)
    metadata={**metadata,'captured_at':.1}
    OverlayController._analysis_completed(c,result,players,metadata)
    assert metadata['published'] and metadata['confirmation_status']=='confirmed'
    assert metadata['published_kind']=='reference' and c.apply_analysis.call_args.args[0].reference


def test_missing_geometry_clears_marks_even_with_a_model_candidate():
    from test_learned_controller import controller,roster
    from lisha_overlay.app import OverlayController
    c=controller();players=roster()
    metadata=dict(generation=4,captured_at=0.,vision_backend='learned',
                  diagnostics={p.player_key:dict(localized=p.player_key!='g5p4') for p in players})
    OverlayController._analysis_completed(c,AnalysisResult('候选方案',status='solved'),players,metadata)
    assert not metadata['published']
    c.apply_analysis.assert_not_called();c.scan.set_annotations.assert_called_with([])
    c.panel.set_summary.assert_called_with('格子待定位')
