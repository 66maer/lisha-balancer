"""Portable batched CPU recognition; no training packages, OCR, or Torch."""
from __future__ import annotations

from collections import defaultdict,OrderedDict
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def _softmax(values):
    values = np.exp(values-values.max(axis=1, keepdims=True))
    return values/values.sum(axis=1, keepdims=True)


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    intersection = max(0, min(ax+aw, bx+bw)-max(ax, bx))*max(0, min(ay+ah, by+bh)-max(ay, by))
    return intersection/max(aw*ah+bw*bh-intersection, 1)


def _patch(image, box, *, font=False):
    x, y, w, h = box
    extra = max(2, round(max(w, h, image.shape[0])*.26 if font else max(w, h)*.16))
    x1, y1 = max(0, int(x)-1), max(0, int(y)-1)
    x2 = min(image.shape[1], int(x+w)+extra)
    y2 = image.shape[0] if font else min(image.shape[0], int(y+h)+extra)
    crop = image[y1:y2, x1:x2, :3]
    if min(crop.shape[:2]) < 5:
        raise ValueError('Buff 图标裁片不完整。')
    return cv2.resize(crop, (40, 40), interpolation=cv2.INTER_AREA if crop.shape[0] > 40 else cv2.INTER_LINEAR)


def _counter_patch(image, box):
    x, y, w, h = map(int, box)
    height, width = image.shape[:2]
    x1 = max(0, x-1)
    expected = min(width-1, x+w-1)
    start, end = max(x1+5, x+round(w*.65)), min(width, x+round(w*1.75))
    y1 = max(0, y)
    y2 = min(height, y1+max(5, round(h*.6)))
    right = expected
    if end > start and y2 > y1:
        hsv = cv2.cvtColor(image[y1:y2, start:end, :3], cv2.COLOR_BGR2HSV)
        support = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 170)).mean(axis=0)
        candidates = np.flatnonzero(support >= .6)+start
        if len(candidates):
            right = min(candidates, key=lambda col: (abs(int(col)-expected), -support[col-start]))
    x2 = min(width, max(x1+5, int(right)+3))
    crop = image[:height, x1:x2, :3]
    if min(crop.shape[:2]) < 5:
        raise ValueError('Buff 数字裁片不完整。')
    return cv2.resize(crop, (40, 40), interpolation=cv2.INTER_AREA if crop.shape[0] > 40 else cv2.INTER_LINEAR)


def _edges(image):
    height = image.shape[0]
    edge = cv2.Canny(cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY), 30, 90)
    horizontal = cv2.morphologyEx(edge, cv2.MORPH_OPEN, np.ones((1, max(8, round(height*.5))), np.uint8))
    contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return edge, [cv2.boundingRect(c) for c in contours]


def _measure_side(images, edge_lookup):
    height = images[0].shape[0]
    widths = [w for image in images for x, y, w, h in edge_lookup(image)[1]
              if y <= height*.25 and .65*height <= w <= 1.1*height]
    candidates = []
    for center in range(round(height*1.05), round(height*.65)-1, -1):
        cluster = [w for w in widths if abs(w-center) <= 1]
        if len(cluster) >= max(2, len(images)*.08):
            candidates.append((len(cluster), float(np.mean(cluster))))
    return round(max(candidates)[1])+1 if candidates else round(height*.9)


def _measure_top(images, side, edge_lookup):
    votes = defaultdict(int)
    for image in images:
        for x, y, w, h in edge_lookup(image)[1]:
            if abs(w-side) <= 3 and y < image.shape[0]*.25:
                votes[y] += 1
    return max(0, min(votes, key=lambda y: (-votes[y], y))-1) if votes else 0


def _proposals(image, side, edge_data):
    height, width = image.shape[:2]
    edge, lines = edge_data
    found = [[max(0, x-1), max(0, y-1), side, side] for x, y, w, h in lines
             if abs(w-side) <= 3 and y < height*.2]
    contours, _ = cv2.findContours(edge, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if y < height*.25 and .83*side <= w <= 1.12*side and .83*side <= h <= 1.12*side:
            found.append([x, y, side, side])
    found.extend([x, 0, side, side] for x in range(0, max(1, width-side+1), side+1))
    kept = []
    for box in found:
        if not any(_iou(box, prior) > .45 for prior in kept):
            kept.append(box)
    return sorted(kept, key=lambda box: (box[0], box[1]))


def _texture(image):
    gray = cv2.cvtColor(image[4:32, 4:34, :3], cv2.COLOR_BGR2GRAY)
    return float(np.abs(cv2.Laplacian(gray, cv2.CV_32F)).mean())


def _body_colour_fraction(image,kind):
    # The upper interior contains artwork, independent of the Chinese word
    # and the large lower-right counter. Pure menu text has no such picture.
    hsv=cv2.cvtColor(image[4:23,6:34,:3],cv2.COLOR_BGR2HSV)
    hue,saturation,value=cv2.split(hsv)
    colour=((hue<20)|(hue>168)) if kind==1 else ((hue>=88)&(hue<=140))
    return float((colour&(saturation>65)&(value>65)).mean())


class LearnedRecognizer:
    """One ONNX call for every candidate in the supplied team snapshot.

    ``shared_team_scale`` applies to one roster captured from the same UI. It
    avoids treating a one-pixel difference in column width as another scale.
    Offline unrelated crops keep shape-specific scale groups for parity.
    """

    def __init__(self, asset_directory=None, *, threads=2, shared_team_scale=True, scale_offsets=(0,),cache_capacity=0):
        directory = Path(asset_directory) if asset_directory else Path(__file__).parent/'assets'/'learned'
        self.configuration = json.loads((directory/'configuration.json').read_text(encoding='utf8'))
        for filename,key in (('cursor_mask.png','cursor_mask_sha256'),('cursor_rgb.png','cursor_rgb_sha256'),
                             ('cursor_focus.png','cursor_focus_sha256'),('death_word_mask.png','death_word_mask_sha256')):
            if key in self.configuration and (not (directory/filename).is_file() or
                    hashlib.sha256((directory/filename).read_bytes()).hexdigest()!=self.configuration[key]):
                raise ValueError('识别参考图校验失败，请恢复完整资源文件。')
        model_path = directory/'model.onnx'
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != self.configuration['model_sha256']:
            raise ValueError('识别模型校验失败，请恢复完整模型文件。')
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        options.add_session_config_entry('session.intra_op.allow_spinning', '0')
        options.add_session_config_entry('session.inter_op.allow_spinning', '0')
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=['CPUExecutionProvider'])
        self.shared_team_scale = shared_team_scale
        self.scale_offsets = tuple(scale_offsets)
        self.cache_capacity=max(0,int(cache_capacity));self._raw_cache=OrderedDict();self.last_inference={}
        if not self.scale_offsets or any(not isinstance(v,int) or abs(v)>3 for v in self.scale_offsets):
            raise ValueError('Invalid icon scale views')

    def clear_cache(self):
        self._raw_cache.clear()

    def _infer(self,pixels,fonts,counters):
        count=len(pixels)
        if not self.cache_capacity:
            feeds={name:np.ascontiguousarray(np.stack(values).transpose(0,3,1,2),dtype=np.float32)/255.
                   for name,values in (('icons',pixels),('font_icons',fonts),('counter_icons',counters))}
            self.last_inference={'candidates':count,'evaluated':count,'cache_hits':0,'calls':1}
            return self.session.run(None,feeds)
        # Only the exact three pixel inputs define a cached network result.
        # Geometry, ownership and all occlusion checks are evaluated anew.
        keys=[];resolved={};missing={}
        for index,views in enumerate(zip(pixels,fonts,counters)):
            fingerprint=hashlib.sha256(b''.join(view.tobytes() for view in views)).digest();keys.append(fingerprint)
            if fingerprint in self._raw_cache:
                resolved[fingerprint]=self._raw_cache[fingerprint];self._raw_cache.move_to_end(fingerprint)
            elif fingerprint not in missing:missing[fingerprint]=index
        if missing:
            indices=list(missing.values())
            feeds={name:np.ascontiguousarray(np.stack([values[i] for i in indices]).transpose(0,3,1,2),dtype=np.float32)/255.
                   for name,values in (('icons',pixels),('font_icons',fonts),('counter_icons',counters))}
            outputs=self.session.run(None,feeds)
            for row,key in enumerate(missing):
                result=tuple(output[row].copy() for output in outputs)
                resolved[key]=result;self._raw_cache[key]=result
                while len(self._raw_cache)>self.cache_capacity:self._raw_cache.popitem(last=False)
        self.last_inference={'candidates':count,'evaluated':len(missing),'cache_hits':count-len(missing),'calls':int(bool(missing))}
        return [np.stack([resolved[key][head] for key in keys]) for head in range(3)]

    def _combine_scales(self, rows, count):
        if len(self.scale_offsets)==1:return rows
        combined=[]
        for owner in range(count):
            variants=[rows[owner+view*count] for view in range(len(self.scale_offsets))]
            signatures=[(r['state'],r['isolated'],r['isolation_confirmed']) for r in variants]
            winner=max(signatures,key=signatures.count)
            votes=signatures.count(winner)
            # Prefer the ensemble majority; with no majority, retain the
            # strongest current prediction instead of erasing all candidates.
            choices=[r for r,s in zip(variants,signatures) if s==winner] if votes*2>len(variants) else variants
            selected=max(choices,key=lambda r:r['confidence']).copy()
            selected['scale_agreement']=[votes,len(variants)]
            selected['scale_variants']=[dict(state=r['state'],isolated=r['isolated'],side=r['icon_side'],
                                             confidence=r['confidence']) for r in variants]
            selected['unverified_scale']=votes<len(variants)
            combined.append(selected)
        return combined

    def predict(self, images):
        if not images:
            return []
        groups = defaultdict(list)
        group_key = lambda image: 0 if self.shared_team_scale else image.shape[:2]
        edge_cache = {id(image): _edges(image) for image in images}
        lookup = lambda image: edge_cache[id(image)]
        for i, image in enumerate(images):
            groups[group_key(image)].append(i)
        sizes = {key: (self.fixed_icon_side if getattr(self,'fixed_icon_side',None) else _measure_side([images[i] for i in ids], lookup)) for key, ids in groups.items()}
        pixels, fonts, counters, owners, boxes = [], [], [], [], []
        geometry = self.configuration['proposal_geometry']
        for view,offset in enumerate(self.scale_offsets):
            tops={key:_measure_top([images[i] for i in ids],sizes[key]+offset,lookup) for key,ids in groups.items()}
            for owner, image in enumerate(images):
                key = group_key(image)
                for box in _proposals(image, sizes[key]+offset, lookup(image)):
                    if abs(box[1]-tops[key]) > geometry['maximum_top_deviation']:
                        box = [box[0], tops[key], box[2], box[3]]
                    x, y, w, h = box
                    core = image[max(0, y+3):min(image.shape[0], y+int(h*.8)),
                                 max(0, x+3):min(image.shape[1], x+int(w*.85)), :3]
                    if core.size and np.ptp(core, axis=(0, 1)).max() <= 4:
                        continue
                    normalized = _patch(image, box)
                    if _texture(normalized) < geometry['minimum_texture']:
                        continue
                    pixels.append(normalized)
                    fonts.append(_patch(image, box, font=True))
                    counters.append(_counter_patch(image, box))
                    owners.append(owner+view*len(images))
                    boxes.append(box)
        rows = [{'state': 'none', 'isolated': False, 'confidence': 0., 'candidates': [],
                 'isolation_confirmed': True, 'unverified_isolation': False,
                 'icon_side': sizes[group_key(image)]+offset} for offset in self.scale_offsets for image in images]
        if not pixels:
            self.last_inference={'candidates':0,'evaluated':0,'cache_hits':0,'calls':0}
            return self._combine_scales(rows,len(images))
        raw_kind, raw_digit, support = self._infer(pixels,fonts,counters)
        kind, digit = _softmax(raw_kind), _softmax(raw_digit)
        kinds, digits = kind.argmax(1), digit.argmax(1)
        for p, (owner, k, d) in enumerate(zip(owners, kinds, digits)):
            row = rows[owner]
            if k == 3:
                row['isolated'] = True
            if k in (1, 2):
                confidence = float(kind[p, k]*digit[p, d])
                body_colour = _body_colour_fraction(pixels[p],int(k))
                state = ('red_' if k == 1 else 'black_')+str(int(d)+1)
                row['candidates'].append({'state': state, 'confidence': confidence, 'bbox': boxes[p],
                    'kind_probability': kind[p].tolist(), 'digit_probability': digit[p].tolist()})
                row['candidates'][-1]['body_colour_fraction']=body_colour
                if confidence > row['confidence']:
                    row.update(state=state, confidence=confidence,body_colour_fraction=body_colour,selected_bbox=boxes[p])
        owners = np.array(owners)
        support = support.reshape(-1)
        isolation_floor = self.configuration['isolation_body_support_min']
        for owner, row in enumerate(rows):
            indices = np.flatnonzero((owners == owner) & (kinds == 3))
            uncertain = bool(len(indices) and not any(support[i] >= isolation_floor for i in indices))
            selected = kind[owners == owner]
            row.update(method='batched_icon_word_own_counter',
                       best_target_score=float((selected[:, 1]+selected[:, 2]).max()) if len(selected) else 0.,
                       best_isolation_score=float(selected[:, 3].max()) if len(selected) else 0.,
                       isolation_confirmed=not uncertain, unverified_isolation=uncertain)
            if row['state']=='none' and len(selected):
                # An absence/isolation view also needs a probability when
                # resolving disagreement. Use its weakest winning class score.
                row['confidence']=float(selected.max(axis=1).min())
            body_floor=self.configuration.get('minimum_target_body_colour',0.)
            unsupported=row.get('body_colour_fraction',1.)<body_floor
            row['unverified_body']=unsupported
            if unsupported:
                row['unverified_state_candidate']=row['state']
        return self._combine_scales(rows,len(images))
