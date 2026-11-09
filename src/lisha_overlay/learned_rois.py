"""Full-width Buff bands anchored to picture edges, independent of font size."""
import cv2
import numpy as np
from collections import defaultdict


def alignment_evidence(image, cells):
    """Check saved cell boundaries each frame without a global grid search.

    Buff text is not used as alignment evidence. Small raster-fitting errors
    are allowed; a disappeared/moved panel cannot become an all-empty roster.
    """
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    horizontal = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)) > 45
    vertical = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)) > 30
    height, width = gray.shape
    result = {}
    for cell in cells:
        rect = cell.rect
        left, right = max(0, round(rect.left())), min(width, round(rect.right()))
        top, bottom = max(0, round(rect.top())), min(height, round(rect.bottom()))
        radius = max(3, round(rect.height()*.14))
        horizontal_support = []
        for y in (top, bottom):
            strip = horizontal[max(0, y-radius):min(height, y+radius+1), left+3:right-3]
            horizontal_support.append(float(strip.mean(axis=1).max()) if strip.size else 0.)
        vertical_support = []
        for x in (left, right):
            strip = vertical[top:bottom, max(0, x-4):min(width, x+5)]
            vertical_support.append(float(strip.max(axis=1).mean()) if strip.size else 0.)
        result[cell.key] = min(horizontal_support) >= .45 and min(vertical_support) >= .25
    return result


def _top_edges(image, cell_xyxy):
    x1, y1, x2, y2 = map(float, cell_xyxy)
    w, h = x2-x1, y2-y1
    height, width = image.shape[:2]
    left, right = max(0, round(x1+w*.015)), min(width, round(x2+w*.025))
    top, bottom = max(0, round(y1+h*.23)), min(height, round(y1+h*.95))
    search = image[top:bottom, left:right, :3]
    if not search.size:
        return []
    edges = cv2.Canny(cv2.cvtColor(search, cv2.COLOR_BGR2GRAY), 30, 90)
    horizontal = cv2.morphologyEx(edges, cv2.MORPH_OPEN,
                                np.ones((1, max(8, round(h*.35))), np.uint8))
    contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [(left+x, top+y, bw) for x, y, bw, bh in map(cv2.boundingRect, contours)
            if .4*h <= bw <= .62*h and y1+h*.26 <= top+y <= y1+h*.50]


def player_bands(image, cells):
    """Use repeated first-slot edges across each column to preserve glyphs."""
    lines = {}
    groups = defaultdict(list)
    for cell in cells:
        rect = cell.rect
        bounds = (rect.left(), rect.top(), rect.right(), rect.bottom())
        lines[cell.key] = _top_edges(image, bounds)
        groups[cell.group].append(cell)
    anchors = {}
    row_fits = {}
    for group, members in groups.items():
        # Text may extend left of the artwork's inner edge. Retain that spill
        # rather than trimming characters to the picture's square boundary.
        votes = [x-2 for cell in members for x, y, w in lines[cell.key]
                 if cell.rect.left()+cell.rect.width()*.015 <= x <= cell.rect.left()+cell.rect.height()*.20]
        if votes:
            # The horizontal Canny edge starts inside the outer picture rim.
            anchors[group] = min(votes, key=lambda x: (-sum(abs(x-y) <= 1 for y in votes), x))
        points = []
        for cell in members:
            if lines[cell.key]:
                x, y, w = max(lines[cell.key], key=lambda line: (line[2], -abs(line[1]-cell.rect.top()-cell.rect.height()*.37)))
                points.append((cell.slot, y-1))
        if len(points) >= 3:
            pitch = float(np.median([(b[1]-a[1])/(b[0]-a[0]) for a in points for b in points if b[0] > a[0]]))
            origin = float(np.median([y-slot*pitch for slot, y in points]))
            typical = float(np.median([cell.rect.height() for cell in members]))
            if abs(pitch-typical) <= typical*.12 and max(abs(y-origin-slot*pitch) for slot, y in points) <= 3:
                row_fits[group] = origin, pitch
    result = []
    for cell in cells:
        rect = cell.rect
        fit = row_fits.get(cell.group)
        result.append(player_band(image, (rect.left(), rect.top(), rect.right(), rect.bottom()),
                                  left_hint=anchors.get(cell.group), top_edges=lines[cell.key],
                                  top_hint=fit[0]+cell.slot*fit[1] if fit else None))
    return result


def player_band(image, cell_xyxy, *, left_hint=None, top_edges=None, top_hint=None):
    x1, y1, x2, y2 = map(float, cell_xyxy)
    w, h = x2-x1, y2-y1
    height, width = image.shape[:2]
    left = max(0, round(left_hint if left_hint is not None else x1+w*.045))
    right = min(width, round(x2+w*.025))
    top = max(0, round(top_hint if top_hint is not None else y1+h*.37))
    candidates = [(bw, -abs(y-top), y-1) for x, y, bw in
                  (_top_edges(image, cell_xyxy) if top_edges is None else top_edges)]
    if candidates:
        top = max(candidates)[2]
    bottom = min(height, round(top+h*.60))
    if right-left < 8 or bottom-top < 8:
        raise ValueError('团队格子的 Buff 区域超出截图。')
    return image[top:bottom, left:right, :3], [left, top, right, bottom]
