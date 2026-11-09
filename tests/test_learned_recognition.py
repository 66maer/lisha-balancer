import json
from collections import OrderedDict
import numpy as np
import pytest
from PySide6.QtGui import QImage
from lisha_overlay.learned_recognition import LearnedRecognizer
from lisha_overlay.team_layout import TeamLayout
from lisha_overlay.vision import VisionEngine


def test_portable_asset_hash_is_checked_before_loading_a_session(tmp_path):
    (tmp_path/'configuration.json').write_text(json.dumps({'model_sha256':'0'*64}),encoding='utf8')
    (tmp_path/'model.onnx').write_bytes(b'changed')
    with pytest.raises(ValueError,match='校验失败'):
        LearnedRecognizer(tmp_path)


def test_dynamic_batch_outputs_agree_with_individual_inference():
    recognizer=LearnedRecognizer()
    pixels=np.random.default_rng(345).uniform(0,1,(1,3,40,40)).astype(np.float32)
    feeds={k:pixels for k in ('icons','font_icons','counter_icons')}
    one=recognizer.session.run(None,feeds)
    many=recognizer.session.run(None,{k:np.repeat(pixels,25,axis=0) for k in feeds})
    assert len(one)==len(many)==3
    for a,b in zip(one,many):
        assert np.isfinite(b).all()
        np.testing.assert_allclose(b,np.repeat(a,25,axis=0),atol=2e-5,rtol=2e-5)


def test_missing_panel_cannot_become_twenty_five_confirmed_empty_players():
    image=QImage(650,500,QImage.Format.Format_RGB32)
    image.fill(0xff202020)
    engine=VisionEngine(TeamLayout(),backend='learned')
    players=engine.analyze(image)
    assert len(players)==25
    assert all(p.color=='unknown' for p in players)
    assert not any(d['localized'] for d in engine.diagnostics.values())


def test_three_scale_views_of_the_roster_use_one_onnx_call():
    recognizer=LearnedRecognizer(scale_offsets=(-1,0,1))
    actual=recognizer.session;calls=[]
    class Session:
        def run(self,names,feeds):
            calls.append(feeds['icons'].shape[0])
            return actual.run(names,feeds)
    recognizer.session=Session();recognizer.fixed_icon_side=31
    images=[np.random.default_rng(i).integers(0,256,(38,128,3),dtype=np.uint8) for i in range(25)]
    rows=recognizer.predict(images)
    assert len(calls)==1 and calls[0]>75
    assert len(rows)==25
    assert all(r['scale_agreement'][1]==3 for r in rows)


def test_scale_disagreement_retains_the_highest_probability_whole_prediction():
    recognizer=LearnedRecognizer(scale_offsets=(-1,0,1))
    variants=[dict(state=s,isolated=iso,isolation_confirmed=True,confidence=score,icon_side=30+i)
              for i,(s,iso,score) in enumerate((('red_2',True,.6),('black_1',False,.7),('red_2',False,.9)))]
    row=recognizer._combine_scales(variants,1)[0]
    assert row['state']=='red_2' and row['isolated'] is False and row['unverified_scale']
    assert row['confidence']==.9


def test_text_only_positive_keeps_model_choice_and_an_advisory():
    recognizer=LearnedRecognizer()
    recognizer.configuration['minimum_target_body_colour']=.02
    class Session:
        def run(self,names,feeds):
            n=len(feeds['icons']);kind=np.tile([-10,10,-10,-10],(n,1)).astype(np.float32)
            digit=np.tile([10,-10,-10,-10],(n,1)).astype(np.float32)
            return [kind,digit,np.zeros((n,1),np.float32)]
    recognizer.session=Session()
    gray=np.random.default_rng(9).integers(0,210,(38,128,1),dtype=np.uint8)
    row=recognizer.predict([np.repeat(gray,3,axis=2)])[0]
    assert row['state']=='red_1' and row['unverified_body']


def test_target_colour_floor_cannot_replace_the_independent_isolation_floor():
    recognizer=LearnedRecognizer();recognizer.configuration['minimum_target_body_colour']=.02
    recognizer.configuration['isolation_body_support_min']=.001
    class Session:
        def run(self,names,feeds):
            n=len(feeds['icons']);kind=np.tile([-10,-10,-10,10],(n,1)).astype(np.float32)
            return [kind,np.tile([10,-10,-10,-10],(n,1)).astype(np.float32),np.full((n,1),.01,np.float32)]
    recognizer.session=Session()
    images=[np.repeat(np.random.default_rng(i).integers(0,210,(38,128,1),dtype=np.uint8),3,axis=2) for i in range(3)]
    assert all(r['isolated'] is True for r in recognizer.predict(images))


def cached_session(capacity=2):
    """Deterministic outputs expose mixing or dropping any of the three inputs."""
    calls=[]
    class Session:
        def run(self,names,feeds):
            calls.append(len(feeds['icons']))
            return [feeds[name].sum(axis=(1,2,3))[:,None]
                    for name in ('icons','font_icons','counter_icons')]
    recognizer=object.__new__(LearnedRecognizer)
    recognizer.cache_capacity=capacity
    recognizer._raw_cache=OrderedDict()
    recognizer.last_inference={}
    recognizer.session=Session()
    return recognizer,calls


def test_exact_cache_checks_body_font_and_counter_pixels_independently():
    recognizer,calls=cached_session(8)
    inputs=[[np.zeros((40,40,3),np.uint8)] for _ in range(3)]
    first=recognizer._infer(*inputs)
    again=recognizer._infer(*inputs)
    assert calls==[1]
    for a,b in zip(first,again):np.testing.assert_array_equal(a,b)
    for view in range(3):
        changed=[[image.copy() for image in images] for images in inputs]
        changed[view][0][0,0,0]=1
        output=recognizer._infer(*changed)
        assert output[view][0,0]>first[view][0,0]
    assert calls==[1,1,1,1]


def test_batch_deduplication_preserves_order_and_lru_eviction_is_bounded():
    recognizer,calls=cached_session(2)
    images=[np.full((40,40,3),v,np.uint8) for v in (1,1,2,3,1)]
    outputs=recognizer._infer(images,images,images)
    assert calls==[3] and len(recognizer._raw_cache)==2
    for output in outputs:
        np.testing.assert_allclose(output[:,0],np.array([1,1,2,3,1])*4800/255.,rtol=1e-6)
    # 1 was the least recently inserted unique input and was evicted.
    recognizer._infer([images[0]],[images[0]],[images[0]])
    assert calls==[3,1] and len(recognizer._raw_cache)==2
    recognizer.clear_cache()
    assert not recognizer._raw_cache


def test_cached_and_uncached_real_predictions_have_identical_state_and_isolation():
    recognizer=LearnedRecognizer(scale_offsets=(-1,0,1))
    images=[np.random.default_rng(i).integers(0,256,(38,128,3),dtype=np.uint8) for i in range(5)]
    uncached=recognizer.predict(images)
    recognizer.cache_capacity=2048
    cached=recognizer.predict(images)
    repeated=recognizer.predict(images)
    state=lambda rows:[(r['state'],r['isolated'],r['isolation_confirmed']) for r in rows]
    assert state(uncached)==state(cached)==state(repeated)
    assert recognizer.last_inference['evaluated']==0


def test_cached_network_does_not_skip_new_occlusions_or_preserve_old_image_geometry():
    image=QImage(650,500,QImage.Format.Format_RGB32);image.fill(0xff202020)
    engine=VisionEngine(TeamLayout(),backend='learned')
    calls=[]
    class Occlusion:
        def find(self,*args):
            calls.append(1)
            return [] if len(calls)==1 else [[0,0,650,500]]
    engine._cursor_occlusion=Occlusion()
    engine.analyze(image);engine.analyze(image)
    assert len(calls)==2
    assert all(d['unverified_occlusion'] for d in engine.diagnostics.values())
    engine._learned_band_bounds['stale']=(0,0,1,1)
    engine._learned_icon_side=99
    resized=QImage(660,510,QImage.Format.Format_RGB32);resized.fill(0xff202020)
    engine.analyze(resized)
    assert 'stale' not in engine._learned_band_bounds
    assert engine._learned_icon_side is None
    assert engine._learned_image_size==(510,660)
