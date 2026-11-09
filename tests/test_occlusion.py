from pathlib import Path
import cv2,numpy as np
from lisha_overlay.occlusion import CursorOcclusionDetector,intersects


def test_cursor_shape_at_different_scales_is_distinct_from_a_teal_skill_block():
    assets=Path(__file__).resolve().parents[1]/'src/lisha_overlay/assets/learned'
    mask=cv2.imdecode(np.fromfile(assets/'cursor_mask.png',dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    detector=CursorOcclusionDetector()
    for scale in (1.,1.5):
        scaled=cv2.resize(mask,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST)>0
        h,w=scaled.shape;image=np.zeros((160,240,3),np.uint8)
        image[30:30+h,20:20+w][scaled]=(160,210,130)
        image[30:70,140:165]=(160,210,130)
        found=detector.find(image,80)
        assert len(found)==1
        x,y,bw,bh=found[0]
        assert abs(x-20)<=1 and abs(y-30)<=1
        assert intersects([15,25,80,100],found[0])
        assert not intersects([100,100,130,130],found[0])


def test_masked_cursor_still_matches_when_its_fill_joins_a_health_bar():
    assets=Path(__file__).resolve().parents[1]/'src/lisha_overlay/assets/learned'
    rgb=cv2.imdecode(np.fromfile(assets/'cursor_rgb.png',dtype=np.uint8),cv2.IMREAD_COLOR)
    fill=cv2.imdecode(np.fromfile(assets/'cursor_mask.png',dtype=np.uint8),cv2.IMREAD_GRAYSCALE)>0
    image=np.zeros((130,210,3),np.uint8);h,w=rgb.shape[:2];image[30:30+h,25:25+w]=rgb
    image[35:80,70:150]=(160,210,130)
    ys,xs=np.where(fill);index=len(xs)//2
    cv2.line(image,(27+int(xs[index]),32+int(ys[index])),(85,32+int(ys[index])),(160,210,130),1)
    found=CursorOcclusionDetector().find(image,65)
    assert found and any(intersects([25,30,25+w,30+h],box) for box in found)
