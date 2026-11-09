"""Literal pink/red 重伤 glyphs, independent of health-bar background colour."""
from pathlib import Path
import hashlib,json
import cv2,numpy as np


def word_ink(image):
    b,g,r=cv2.split(image[:,:,:3].astype(np.float32))
    return np.uint8((r>125)&(g>55)&(b>45)&(r>g*1.15)&(r>b*1.10))*255


class DeathWordDetector:
    def __init__(self,asset_directory=None):
        directory=Path(asset_directory) if asset_directory else Path(__file__).parent/'assets'/'learned'
        self.templates=[];self.scaled={}
        extra=directory/'death_word_mask.png'
        if extra.is_file():
            config=json.loads((directory/'configuration.json').read_text(encoding='utf8'))
            if hashlib.sha256(extra.read_bytes()).hexdigest()!=config.get('death_word_mask_sha256'):
                raise ValueError('重伤字样参考图校验失败。')
            self.templates.append(cv2.imdecode(np.fromfile(extra,dtype=np.uint8),cv2.IMREAD_GRAYSCALE))
        for path in (Path(__file__).parent/'assets'/'buffs').glob('ui_dead*.png'):
            image=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR)
            ink=word_ink(image);ys,xs=np.where(ink)
            if len(xs)>=20:self.templates.append(ink[ys.min():ys.max()+1,xs.min():xs.max()+1])

    def match(self,roi,cell_height):
        ink=word_ink(roi)
        if np.count_nonzero(ink)<20:return False,0.
        best=0.
        for index,reference in enumerate(self.templates):
            for height in range(7,min(40,round(cell_height*.5),ink.shape[0])+1):
                width=round(height*reference.shape[1]/reference.shape[0])
                if width>ink.shape[1] or width<5:continue
                key=index,width,height
                template=self.scaled.get(key)
                if template is None:
                    template=cv2.resize(reference,(width,height),interpolation=cv2.INTER_AREA if height<reference.shape[0] else cv2.INTER_LINEAR)
                    self.scaled[key]=template
                score=float(cv2.matchTemplate(ink,template,cv2.TM_CCOEFF_NORMED).max())
                if np.isfinite(score):best=max(best,score)
        return best>=.88,best
