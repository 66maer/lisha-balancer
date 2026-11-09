"""Synthetic screen data: only the requested selection can reach detection."""
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
import mss
from lisha_overlay.auto_capture import AutoGridWorker
from test_grid_detection import _draw_grid,_assert_cells_match


@pytest.mark.parametrize('formation',[(5,),(3,2),(2,3),(1,4),(4,1)])
@pytest.mark.parametrize('scale',[.8,1.25])
def test_calibration_sees_only_the_selection_with_arbitrary_outside_content(monkeypatch,formation,scale):
    scene=np.full((1100,1800,3),(28,34,31),np.uint8)
    _draw_grid(scene,(5,),x=20,y=50)  # Another complete roster outside the box.
    cv2.line(scene,(1795,0),(1795,1099),(250,250,250),2)
    expected=_draw_grid(scene,formation,x=800,y=100,scale=scale)
    right=max(r.right() for r in expected);bottom=max(r.bottom() for r in expected)
    region=dict(left=780,top=80,width=round(right)-780+20,height=round(bottom)-80+40)
    calls=[]
    class Capture:
        monitors=[{},dict(left=0,top=0,width=1800,height=1100)]
        def grab(self,rect):
            calls.append(dict(rect));x,y,w,h=(rect[k] for k in ('left','top','width','height'))
            pixels=cv2.cvtColor(scene[y:y+h,x:x+w],cv2.COLOR_BGR2BGRA)
            return SimpleNamespace(width=w,height=h,bgra=pixels.tobytes())
        def close(self):pass
    monkeypatch.setattr(mss,'mss',Capture)
    worker=AutoGridWorker();results=[];failures=[]
    worker.completed.connect(lambda *args:results.append(args))
    worker.failed.connect(lambda *args:failures.append(args))
    worker.locate(dict(generation=1,capture_region=region,search_scope='selection'))
    assert len(results)==1 and not failures and calls==[region]
    found,frame,metadata=results[0]
    projected=[r.translated(-region['left'],-region['top']) for r in expected]
    _assert_cells_match(found,projected,(frame.width(),frame.height()),tolerance=9)
    assert metadata['capture_region']==region and metadata['search_scope']=='selection'


@pytest.mark.parametrize('region',[dict(left=780,top=80,width=180,height=540),
                                  dict(left=780,top=750,width=400,height=200)])
def test_incomplete_or_empty_selection_never_falls_back_to_the_full_screen(monkeypatch,region):
    scene=np.full((1100,1800,3),(28,34,31),np.uint8)
    _draw_grid(scene,(5,),x=20,y=50)
    _draw_grid(scene,(3,2),x=800,y=100)
    calls=[]
    class Capture:
        monitors=[{},dict(left=0,top=0,width=1800,height=1100)]
        def grab(self,rect):
            calls.append(dict(rect));x,y,w,h=(rect[k] for k in ('left','top','width','height'))
            pixels=cv2.cvtColor(scene[y:y+h,x:x+w],cv2.COLOR_BGR2BGRA)
            return SimpleNamespace(width=w,height=h,bgra=pixels.tobytes())
        def close(self):pass
    monkeypatch.setattr(mss,'mss',Capture)
    worker=AutoGridWorker();results=[];failures=[]
    worker.completed.connect(lambda *args:results.append(args))
    worker.failed.connect(lambda *args:failures.append(args))
    worker.locate(dict(generation=1,capture_region=region))
    assert not results and len(failures)==1 and calls==[region]
    assert '框内未找到完整25格' in failures[0][0]
