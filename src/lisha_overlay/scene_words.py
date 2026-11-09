"""Known game-menu words are occlusion cues, never player Buff evidence."""
from pathlib import Path
import hashlib,json
import cv2,numpy as np


class SceneWordDetector:
    def __init__(self,asset_directory=None):
        directory=Path(asset_directory) if asset_directory else Path(__file__).parent/'assets'/'learned'
        config=json.loads((directory/'configuration.json').read_text(encoding='utf8'));self.templates=[];self.scaled={}
        for row in config.get('scene_word_templates',[]):
            path=directory/row['file']
            if path.parent!=directory or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('界面遮挡参考图校验失败。')
            self.templates.append((row['word'],cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)))

    def find(self,image,bands,cell_height):
        if not self.templates:return []
        hsv=cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2HSV);fg=np.uint8((hsv[:,:,1]<70)&(hsv[:,:,2]>140))*255
        horizontal=cv2.morphologyEx(fg,cv2.MORPH_OPEN,np.ones((1,max(40,round(cell_height))),np.uint8))
        vertical=cv2.morphologyEx(fg,cv2.MORPH_OPEN,np.ones((max(25,round(cell_height*.5)),1),np.uint8))
        fg=cv2.bitwise_and(fg,cv2.bitwise_not(cv2.bitwise_or(horizontal,vertical)))
        joined=cv2.morphologyEx(fg,cv2.MORPH_CLOSE,np.ones((3,5),np.uint8));contours,_=cv2.findContours(joined,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        gray=cv2.cvtColor(image[:,:,:3],cv2.COLOR_BGR2GRAY);found=[]
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            if not (8<=h<=cell_height*.5 and 20<=w<=min(550,image.shape[1])):continue
            left,top=max(0,x-3),max(0,y-3);right,bottom=min(image.shape[1],x+w+3),min(image.shape[0],y+h+3)
            if not any(min(right,b[2])>max(left,b[0]) and min(bottom,b[3])>max(top,b[1]) for b in bands):continue
            patch=gray[top:bottom,left:right]
            for word,reference in self.templates:
                for height in range(max(7,h-1),h+4):
                    width=round(height*reference.shape[1]/reference.shape[0])
                    if height>patch.shape[0] or width>patch.shape[1]:continue
                    key=word,width,height
                    if key not in self.scaled:self.scaled[key]=cv2.resize(reference,(width,height))
                    scores=cv2.matchTemplate(patch,self.scaled[key],cv2.TM_CCOEFF_NORMED);_,score,_,pos=cv2.minMaxLoc(scores)
                    if score<.91:continue
                    a,b=left+pos[0],top+pos[1];box=[a,b,a+width,b+height]
                    overlap=max((max(0,min(box[2],r[2])-max(box[0],r[0]))*max(0,min(box[3],r[3])-max(box[1],r[1])) for r in bands),default=0)
                    if overlap/(width*height)>=.4:found.append({'bounds':box,'word':word,'confidence':float(score)})
        return found
