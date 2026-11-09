"""Recognize the game's visible arrow shape; never infer covered Buff states."""
import hashlib,json
from pathlib import Path
import cv2,numpy as np


class CursorOcclusionDetector:
    def __init__(self, asset_directory=None):
        directory=Path(asset_directory) if asset_directory else Path(__file__).parent/'assets'/'learned'
        path=directory/'cursor_mask.png';self.reference=None;self.rgb=None;self.focus=None;self.scaled={}
        if not path.is_file():return
        config=json.loads((directory/'configuration.json').read_text(encoding='utf8'))
        if hashlib.sha256(path.read_bytes()).hexdigest()!=config.get('cursor_mask_sha256'):
            raise ValueError('遮挡参考图校验失败。')
        pixels=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
        self.aspect=pixels.shape[1]/pixels.shape[0]
        self.reference=cv2.resize(pixels,(32,48),interpolation=cv2.INTER_NEAREST)>0
        if (directory/'cursor_rgb.png').is_file():
            for filename,key in (('cursor_rgb.png','cursor_rgb_sha256'),('cursor_focus.png','cursor_focus_sha256')):
                if hashlib.sha256((directory/filename).read_bytes()).hexdigest()!=config.get(key):raise ValueError('光标外观参考图校验失败。')
            self.rgb=cv2.imdecode(np.fromfile(directory/'cursor_rgb.png',dtype=np.uint8),cv2.IMREAD_COLOR)
            self.focus=cv2.imdecode(np.fromfile(directory/'cursor_focus.png',dtype=np.uint8),cv2.IMREAD_GRAYSCALE)

    def find(self,image,cell_height):
        if self.reference is None:return []
        b,g,r=cv2.split(image[:,:,:3].astype(np.int16))
        mask=np.uint8((g>r+20)&(g>b+5)&(b>r)&(r>55)&(g>120))
        count,labels,stats,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
        found=[];regions=[]
        for index,(x,y,w,h,area) in enumerate(stats):
            if not index or area<40 or min(w,h)<10:continue
            if w<=cell_height*4 and h<=cell_height*2:
                regions.append([max(0,int(x)-14),max(0,int(y)-14),min(image.shape[1],int(x+w)+14),min(image.shape[0],int(y+h)+14)])
            if max(w,h)>cell_height*1.4:continue
            if abs(w/h-self.aspect)>.17:continue
            shape=cv2.resize(np.uint8(labels[y:y+h,x:x+w]==index),(32,48),interpolation=cv2.INTER_NEAREST)>0
            overlap=np.logical_and(shape,self.reference).sum()/max(1,np.logical_or(shape,self.reference).sum())
            if overlap>=.73:found.append([int(x),int(y),int(w),int(h)])
        if found or self.rgb is None:return found
        # The fill can join a similar-coloured health bar. A masked appearance
        # check includes its dark outline and ignores the covered background.
        best=.10;box=None
        scales=sorted({1.,round(cell_height/64,2),round(cell_height/64*.9,2),round(cell_height/64*1.1,2)})
        for scale in scales:
            width=max(8,round(self.rgb.shape[1]*scale));height=max(10,round(self.rgb.shape[0]*scale));key=width,height
            if key not in self.scaled:
                self.scaled[key]=(cv2.resize(self.rgb,(width,height)),cv2.resize(self.focus,(width,height),interpolation=cv2.INTER_NEAREST))
            template,focus=self.scaled[key]
            for left,top,right,bottom in regions:
                if right-left<width or bottom-top<height:continue
                scores=cv2.matchTemplate(image[top:bottom,left:right,:3],template,cv2.TM_SQDIFF_NORMED,mask=focus)
                scores=np.nan_to_num(scores,nan=1.,posinf=1.,neginf=1.)
                value,_,location,_=cv2.minMaxLoc(scores)
                if value<best:best=value;box=[left+location[0],top+location[1],width,height]
        return [box] if box is not None else []


def intersects(bounds,box):
    left,top,right,bottom=bounds;x,y,w,h=box
    return min(right,x+w)>max(left,x) and min(bottom,y+h)>max(top,y)
