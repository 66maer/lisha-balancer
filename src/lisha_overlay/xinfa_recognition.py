"""One-time heart recognition from the calibrated team panel."""
from collections import Counter
import json

import cv2
import numpy as np
from PySide6.QtCore import QRectF
import onnxruntime as ort

from .learned_rois import alignment_evidence
from .vision import _qimage_bgr
from .xinfa import ASSETS

SIZE = 32
RECURRENCE = 2
MASK_PENALTIES = np.array((0., .04, .04), dtype=np.float32)
GEOMETRY = ((.09, 0., .64), (.17, 0., .64), (-.065, 0., .64), (0., .07, .64),
            (0., -.055, .64), (.04, .04, .54), (.14, .04, .54), (0., 0., .73))


def combine_masks(variants):
    return (variants - MASK_PENALTIES[:variants.shape[-1]]).max(axis=-1)


def rank_templates(scores, labels):
    ordered = np.argsort(-scores, kind='stable')
    leaders = {}
    for index in ordered:
        leaders.setdefault(labels[index], index)
    return list(leaders.values())


class IconMatcher:
    """Use the fixed artwork descriptors exported alongside the classifier."""
    def __init__(self):
        with np.load(ASSETS / 'artwork.npz', allow_pickle=False) as data:
            self.labels = tuple(data['labels'].tolist())
            self.templates = data['templates']
            self.masks = data['masks']
            self.chroma = data['chroma']
            self.unit_x, self.unit_y = data['unit_x'], data['unit_y']
        self.count = len(self.labels)
        self.variants = self.masks.shape[1] // self.count
        self.mask_counts = self.masks.sum(axis=0)

    def match_frame(self, image, cells):
        geometry, relative = [], []
        starts = [0]
        for cell in cells:
            x, y, _, height = cell.rect.getRect()
            step = max(1, round(height * .04))
            offsets_x = np.arange(round(x - height * .12), round(x + height * .16) + 1, step)
            offsets_y = np.arange(round(y - height * .10), round(y + height * .14) + 1, step)
            sizes = sorted({round(height * ratio) for ratio in (.32, .35, .38, .41, .44, .47, .50)})
            positions = np.stack(np.meshgrid(offsets_x, offsets_y, sizes, indexing='ij'), axis=-1).reshape(-1, 3)
            geometry.append(positions)
            relative.append((positions - (x, y, 0)) / height)
            starts.append(starts[-1] + len(positions))
        x, y, side = np.concatenate(geometry).astype(np.float32).T[:, :, None]
        map_x = x + side * self.unit_x - .5
        map_y = y + side * self.unit_y - .5
        patches = cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        patches = patches.astype(np.float32) / 255
        sums = np.moveaxis(patches, -1, 0) @ self.masks
        energy = np.sum(patches * patches, axis=2) @ self.masks
        variance = energy - np.sum(sums * sums, axis=0) / self.mask_counts
        dot = patches.reshape(len(patches), -1) @ self.templates
        matches = np.clip(dot / np.sqrt(np.maximum(variance, .001)), -1, 1)
        captured_chroma = (sums[[0, 2]] - sums[1]) / self.mask_counts
        chroma_distance = np.mean(np.abs(captured_chroma - self.chroma[:, None, :]), axis=0)
        matches -= np.maximum(chroma_distance - .12, 0) * .28
        blocks = [matches[begin:end].reshape(end-begin, self.count, self.variants)
                  for begin, end in zip(starts, starts[1:])]
        anchors = []
        for i, block in enumerate(blocks):
            cls_score = combine_masks(block)
            maxima = cls_score.max(axis=0)
            best, second = rank_templates(maxima, self.labels)[:2]
            if maxima[best] >= .68 and maxima[best] - maxima[second] >= .09:
                position = np.unravel_index(cls_score.argmax(), cls_score.shape)[0]
                anchors.append((cells[i].group, relative[i][position]))
        if len(anchors) < 4:
            return [block.max(axis=0) for block in blocks]
        median = np.median([p for _, p in anchors], axis=0)
        groups = {}
        for group in {cell.group for cell in cells}:
            positions = [p for team, p in anchors if team == group]
            groups[group] = np.median(positions, axis=0) if len(positions) >= 3 else median
        results = []
        for cell, block, positions in zip(cells, blocks, relative):
            disagreement = np.abs(positions - groups[cell.group])
            penalty = np.maximum(disagreement - (.035, .035, .035), 0) @ (.9, .9, 1.15)
            results.append((block - penalty[:, None, None]).max(axis=0))
        return results


def crop_slots(panel, layout, *, dx=0., dy=0., extent=.64):
    height, width = panel.shape[:2]
    for cell in layout.cells(QRectF(0, 0, width, height)):
        x, y, _, row_height = cell.rect.getRect()
        side = max(4, round(extent * row_height))
        left = round(x + (dx - .075) * row_height)
        top = round(y + (dy - .05) * row_height)
        crop = panel[max(0, top):min(top+side, height), max(0, left):min(left+side, width)]
        crop = cv2.copyMakeBorder(crop, max(0, -top), max(0, top+side-height),
                                 max(0, -left), max(0, left+side-width), cv2.BORDER_REPLICATE)
        yield cell.key, cv2.resize(crop, (SIZE, SIZE), interpolation=cv2.INTER_LINEAR)


def decide(frames, classes):
    result = {}
    for key in frames[0]:
        visible = [frame[key] for frame in frames if frame[key] is not None]
        if not visible:
            result[key] = dict(result=None, top=None, votes=0, available=0, per_frame=[])
            continue
        series = np.stack(visible)
        rank = np.argsort(-series, axis=1)
        votes = Counter(int(order[0]) for order, row in zip(rank, series)
                        if row[order[0]] >= .54 and row[order[0]] - row[order[1]] >= .18)
        strongest = int(np.argmax(np.median(series, axis=0)))
        runnerup_votes = max((count for candidate, count in votes.items() if candidate != strongest), default=0)
        agreed = votes[strongest] >= RECURRENCE and votes[strongest] > runnerup_votes
        result[key] = dict(result=classes[strongest] if agreed else None, top=classes[strongest],
                           votes=votes[strongest], available=len(series),
                           per_frame=[(classes[order[0]], float(row[order[0]])) for order, row in zip(rank, series)])
    return result


def reconcile(decisions, observations):
    by_key = {key: [] for key in decisions}
    for frame in observations:
        for row in frame:
            if row['result'] is not None:
                by_key[row['key']].append(row)
    for key, info in decisions.items():
        direct = by_key[key]
        votes = Counter(row['result'] for row in direct)
        if not votes:
            continue
        label, agreements = votes.most_common(1)[0]
        if agreements < 2 or agreements * 4 < len(observations) * 3 or len(votes) > 1:
            continue
        strong = sum(row['score'] >= .61 and row['margin'] >= .15 for row in direct)
        if info['result'] in (label, None):
            if info['top'] == label or strong >= 2:
                info['result'] = label
                info['artwork_support'] = agreements
        elif strong >= 2:
            info['model_choice'] = info['result']
            info['result'] = label
            info['artwork_support'] = agreements


class RosterReader:
    def __init__(self):
        self.classes = tuple(json.loads((ASSETS / 'catalog.json').read_text(encoding='utf-8'))['classes'])
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(ASSETS / 'model.onnx'), options, providers=['CPUExecutionProvider'])
        self.artwork = IconMatcher()

    def read(self, panel, layout):
        height, width = panel.shape[:2]
        cells = layout.cells(QRectF(0, 0, width, height))
        aligned = alignment_evidence(panel, cells)
        if not any(aligned.values()):
            return {cell.key: None for cell in cells}, []
        slots = list(crop_slots(panel, layout))
        crops = [[crop for _, crop in slots]]
        crops.extend([crop for _, crop in crop_slots(panel, layout, dx=x, dy=y, extent=s)]
                     for x, y, s in GEOMETRY)
        inputs = np.ascontiguousarray(np.stack(crops).reshape(-1, SIZE, SIZE, 3).transpose(0, 3, 1, 2))
        logits = self.session.run(None, {'images': inputs})[0]
        scores = np.exp(logits - logits.max(axis=1, keepdims=True))
        views = (scores / scores.sum(axis=1, keepdims=True)).reshape(len(crops), len(slots), -1)
        ranked = np.sort(views, axis=0)
        scores = views[0] * .30 + ranked[-1] * .40 + ranked[-2] * .30
        probabilities = {key: values if aligned[key] else None for (key, _), values in zip(slots, scores)}
        artwork = []
        for cell, candidates in zip(cells, self.artwork.match_frame(panel, cells)):
            scores = combine_masks(candidates)
            best, second = rank_templates(scores, self.artwork.labels)[:2]
            score, margin = float(scores[best]), float(scores[best] - scores[second])
            if aligned[cell.key] and score >= .54 and margin >= .045:
                artwork.append(dict(key=cell.key, result=self.artwork.labels[best], score=score, margin=margin))
        return probabilities, artwork


class RosterScan:
    """Finish after one short opening window, retaining blanks for the review."""
    def __init__(self):
        self.started = None
        self.last = None
        self.frames = []
        self.artwork = []
        self.result = None

    def observe(self, reader, frame, layout, seconds):
        if self.result is not None:
            return self.result
        if self.last is not None and seconds - self.last < .45:
            return None
        if self.started is None:
            self.started = seconds
        self.last = seconds
        probabilities, artwork = reader.read(_qimage_bgr(frame), layout)
        self.frames.append(probabilities)
        self.artwork.append(artwork)
        result = decide(self.frames, reader.classes)
        reconcile(result, self.artwork)
        verified = {key: row['result'] for key, row in result.items() if row['result'] is not None}
        if len(self.frames) >= 7 or seconds - self.started >= 3.0 or (len(self.frames) >= 3 and len(verified) == 25):
            self.result = verified
        return self.result
