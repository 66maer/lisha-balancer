"""Find complete five-column team panels from repeated cell borders.

Detection uses no Buff samples, names, window handles, or desktop input. A
column must have six regularly spaced horizontal boundaries and two supported
vertical sides. Five such columns must form one supported, unambiguous layout.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools

import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage

from .team_layout import GroupGridLayout, TeamLayout


@dataclass(frozen=True)
class GridDetection:
    layout: TeamLayout
    bounds: QRectF
    confidence: float
    reason: str
    image_size: tuple[int, int]

    def layout_for_region(self, region: QRectF) -> TeamLayout:
        """Reproject into a padded capture ROI in the original image's pixels."""
        region = QRectF(region)
        if region.isEmpty():
            raise ValueError("重定位区域必须具有正数尺寸。")
        width, height = self.image_size
        groups = []
        for group in self.layout.groups:
            x, y, w, h = group.rect
            pixels = QRectF(x * width, y * height, w * width, h * height)
            if not region.adjusted(-1e-6, -1e-6, 1e-6, 1e-6).contains(pixels):
                raise ValueError("重定位区域不能裁掉任何团队格子。")
            groups.append(GroupGridLayout(group.group, (
                (pixels.x() - region.x()) / region.width(),
                (pixels.y() - region.y()) / region.height(),
                pixels.width() / region.width(), pixels.height() / region.height(),
            )))
        options = dict(self.layout.grid_options)
        options.update(
            left=(options["left"] * width - region.x()) / region.width(),
            right=(region.right() - (1 - options["right"]) * width) / region.width(),
            top=(options["top"] * height - region.y()) / region.height(),
            bottom=(region.bottom() - (1 - options["bottom"]) * height) / region.height(),
            column_gap=options["column_gap"] * width / region.width(),
            row_gap=options["row_gap"] * height / region.height(),
            footer=options["footer"] * height / region.height(),
        )
        # A region containing all cells may intentionally omit some empty
        # footer; reflect its usable extent without changing cell rectangles.
        options["left"] = max(0.0, options["left"])
        options["top"] = max(0.0, options["top"])
        options["right"] = max(0.0, options["right"])
        options["bottom"] = max(0.0, options["bottom"])
        options["footer"] = min(options["footer"], 0.5 / len(options["formation"]))
        return TeamLayout(tuple(groups), self.layout.name_rect, self.layout.buff_rect,
                          self.layout.marker_rect, options)


@dataclass(frozen=True)
class _Column:
    left: float
    top: float
    width: float
    row_height: float
    score: float

    @property
    def right(self):
        return self.left + self.width

    @property
    def bottom(self):
        return self.top + self.row_height * 5

    @property
    def rect(self):
        return QRectF(self.left, self.top, self.width, self.row_height * 5)


def _bgr(frame: QImage) -> np.ndarray:
    converted = frame.convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(converted.constBits(), np.uint8, count=converted.sizeInBytes()).reshape(
        converted.height(), converted.bytesPerLine()
    )
    pixels = rows[:, :converted.width() * 4].reshape(converted.height(), converted.width(), 4)
    return cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)


def _iou(first: QRectF, second: QRectF) -> float:
    intersection = first.intersected(second)
    area = max(0.0, intersection.width()) * max(0.0, intersection.height())
    union = first.width() * first.height() + second.width() * second.height() - area
    return area / union if union > 0 else 0.0


def _borders(vertical: np.ndarray) -> list[int]:
    ink = vertical > 0
    strength = ink.sum(axis=0)
    # Repeated Buff/icon sides add up to a strong column, but each stroke is
    # short. A team-frame side continues across several player rows.
    run = np.zeros(vertical.shape[1], dtype=np.int32)
    longest = np.zeros_like(run)
    for row in ink:
        run = np.where(row, run + 1, 0)
        longest = np.maximum(longest, run)
    threshold = max(25, float(strength.max(initial=0)) * 0.12)
    positions = np.flatnonzero((strength >= threshold) &
                               (longest >= max(60, vertical.shape[0] * 0.04)))
    clusters: list[list[int]] = []
    for position in positions:
        if clusters and position - clusters[-1][-1] <= 4:
            clusters[-1].append(int(position))
        else:
            clusters.append([int(position)])
    return [round(float(np.average(cluster, weights=longest[cluster]))) for cluster in clusters]


def _column_candidates(horizontal: np.ndarray, vertical: np.ndarray) -> list[_Column]:
    height, width = horizontal.shape
    borders = _borders(vertical)
    columns = []
    for index, left in enumerate(borders):
        for right in borders[index + 1:index + 5]:
            column_width = right - left
            if not 45 <= column_width <= min(360, width / 1.2):
                continue
            profile = horizontal[:, left + 2:right - 1].mean(axis=1).astype(np.float32)
            # A pair of long decorative vertical lines is not a player column.
            # Reject it before trying dozens of pitches over a full monitor.
            # Six independent strong horizontal runs are necessary for five
            # cells, regardless of image height or background edge count.
            strong = profile >= 0.60
            runs = int(np.count_nonzero(strong & ~np.r_[False, strong[:-1]]))
            if runs < 6:
                continue
            profile = cv2.dilate(profile[:, None], np.ones((3, 1), np.uint8)).ravel()
            left_v = (vertical[:, max(0, left - 4):min(width, left + 5)].max(axis=1) > 0).astype(float)
            right_v = (vertical[:, max(0, right - 4):min(width, right + 5)].max(axis=1) > 0).astype(float)
            left_integral = np.r_[0.0, np.cumsum(left_v)]
            right_integral = np.r_[0.0, np.cumsum(right_v)]
            pair_candidates = []
            minimum = max(16.0, column_width * 0.30)
            maximum = min(120.0, column_width * 0.58, (height - 1) / 5)
            # Raster pitch must not inherit an arbitrary fractional phase from
            # the unrelated horizontal column width.
            for row_height in np.arange(np.ceil(minimum * 2) / 2, maximum + 0.01, 0.5):
                offsets = np.rint(np.arange(6) * row_height).astype(int)
                count = height - offsets[-1]
                if count <= 0:
                    continue
                rows = np.stack([profile[offset:offset + count] for offset in offsets])
                mean = rows.mean(axis=0)
                minimum_edge = rows.min(axis=0)
                starts = np.arange(count)
                ends = starts + offsets[-1]
                left_support = (left_integral[ends] - left_integral[starts]) / offsets[-1]
                right_support = (right_integral[ends] - right_integral[starts]) / offsets[-1]
                valid = (minimum_edge >= 0.45) & (mean >= 0.70) & (left_support >= 0.25) & (right_support >= 0.25)
                score = mean * 0.80 + (left_support + right_support) * 0.10
                score[~valid] = -1
                for top in np.argsort(score)[-6:]:
                    if score[top] >= 0.70:
                        pair_candidates.append(_Column(left, float(top), column_width,
                                                       float(row_height), float(score[top])))
            selected = []
            for candidate in sorted(pair_candidates, key=lambda item: item.score, reverse=True):
                if all(_iou(candidate.rect, previous.rect) < 0.82 for previous in selected):
                    selected.append(candidate)
            columns.extend(selected[:10])
    return columns


def _row_chains(columns: list[_Column]) -> list[tuple[_Column, ...]]:
    rows: list[tuple[_Column, ...]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for anchor in columns:
        compatible = [column for column in columns
                      if abs(column.top - anchor.top) <= max(3, anchor.row_height * 0.16)
                      and abs(column.row_height - anchor.row_height) <= anchor.row_height * 0.10
                      and abs(column.width - anchor.width) <= anchor.width * 0.15]
        selected = []
        for column in sorted(compatible, key=lambda item: item.score, reverse=True):
            if all(abs(column.left - previous.left) > anchor.width * 0.45 for previous in selected):
                selected.append(column)
        selected.sort(key=lambda item: item.left)
        for start in range(len(selected)):
            chain = [selected[start]]
            for following in selected[start + 1:start + 5]:
                gap = following.left - chain[-1].right
                if not -anchor.width * 0.08 <= gap <= anchor.width * 0.20:
                    break
                chain.append(following)
            for length in range(1, len(chain) + 1):
                row = tuple(chain[:length])
                key = tuple((round(item.left / 3), round(item.top / 3)) for item in row)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
    return rows


def _bounds(columns: tuple[_Column, ...], footer: float = 0) -> QRectF:
    left = min(column.left for column in columns)
    top = min(column.top for column in columns)
    right = max(column.right for column in columns)
    bottom = max(column.bottom for column in columns) + footer
    return QRectF(left, top, right - left, bottom - top)


def _hypotheses(columns: list[_Column]) -> list[tuple[float, tuple[tuple[_Column, ...], ...]]]:
    rows = _row_chains(columns)
    hypotheses = [(float(np.mean([column.score for column in row])), (row,))
                  for row in rows if len(row) == 5]
    for upper, lower in itertools.combinations(rows, 2):
        if len(upper) + len(lower) != 5:
            continue
        if upper[0].top > lower[0].top:
            upper, lower = lower, upper
        row_height = float(np.median([column.row_height for column in (*upper, *lower)]))
        width = float(np.median([column.width for column in (*upper, *lower)]))
        gap = lower[0].top - float(np.median([column.bottom for column in upper]))
        if not row_height * 0.05 <= gap <= row_height * 1.5:
            continue
        if abs(upper[0].left - lower[0].left) > width * 0.15:
            continue
        if any(abs(column.row_height - row_height) > row_height * 0.08
               or abs(column.width - width) > width * 0.13 for column in (*upper, *lower)):
            continue
        aligned = min(len(upper), len(lower))
        if any(abs(upper[index].left - lower[index].left) > width * 0.15 for index in range(aligned)):
            continue
        score = float(np.mean([column.score for column in (*upper, *lower)]))
        hypotheses.append((score, (upper, lower)))
    unique = []
    for score, rows in sorted(hypotheses, key=lambda item: item[0], reverse=True):
        flat = tuple(column for row in rows for column in row)
        if any(_iou(_bounds(flat), _bounds(tuple(column for row in previous for column in row))) > 0.80
               for _, previous in unique):
            continue
        unique.append((score, rows))
    return unique


def detect_team_grid(image: QImage, *, _resample_retry: bool = True) -> GridDetection | None:
    """Return one complete team grid or decline ambiguous/incomplete input."""
    if not isinstance(image, QImage) or image.isNull() or min(image.width(), image.height()) < 100:
        return None
    pixels = _bgr(image)
    gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 90)
    vertical = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((15, 1), np.uint8))
    vertical = cv2.morphologyEx(vertical, cv2.MORPH_CLOSE, np.ones((5, 1), np.uint8))
    horizontal = (np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)) > 45).astype(np.float32)
    columns = _column_candidates(horizontal, vertical)
    hypotheses = _hypotheses(columns)
    if not hypotheses or hypotheses[0][0] < 0.75:
        if _resample_retry:
            # Thin borders in a resized recording can fall between pixels.
            # A second raster phase recovers line continuity for detection;
            # it never changes the original pixels used to read Buffs.
            enlarged = image.scaled(round(image.width() * 1.5), round(image.height() * 1.5),
                                    Qt.AspectRatioMode.IgnoreAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            found = detect_team_grid(enlarged, _resample_retry=False)
            if found is not None:
                sx, sy = image.width() / enlarged.width(), image.height() / enlarged.height()
                bounds = found.bounds
                return GridDetection(found.layout,
                                     QRectF(bounds.x() * sx, bounds.y() * sy,
                                            bounds.width() * sx, bounds.height() * sy),
                                     found.confidence, found.reason,
                                     (image.width(), image.height()))
        return None
    score, rows = hypotheses[0]
    if (len(hypotheses) > 1 and hypotheses[1][0] >= 0.75
            and hypotheses[0][0] - hypotheses[1][0] < 0.05):
        return None
    flat = tuple(column for row in rows for column in row)
    row_height = float(np.median([column.row_height for column in flat]))
    if len(rows) == 2:
        footer = max(0.0, float(np.median([column.top for column in rows[1]]))
                     - float(np.median([column.bottom for column in rows[0]])))
    else:
        footer = row_height * 0.45
    footer = min(footer, row_height * 0.75)
    bounds = _bounds(flat, footer).intersected(QRectF(image.rect()))
    if bounds.isEmpty():
        return None
    width, height = image.width(), image.height()
    groups = tuple(GroupGridLayout(index + 1, (
        column.left / width, column.top / height,
        column.width / width, column.row_height * 5 / height,
    )) for index, column in enumerate(flat))
    gaps = [row[index + 1].left - row[index].right for row in rows for index in range(len(row) - 1)]
    grid_options = {
        "formation": tuple(len(row) for row in rows),
        "left": bounds.left() / width, "top": bounds.top() / height,
        "right": max(0.0, 1 - bounds.right() / width),
        "bottom": max(0.0, 1 - bounds.bottom() / height),
        "column_gap": max(0.0, float(np.median(gaps)) / width) if gaps else 0.0,
        "row_gap": 0.0, "footer": min(footer / height, 0.20),
    }
    layout = TeamLayout(groups=groups, grid_options=grid_options)
    return GridDetection(layout, bounds, min(0.99, score),
                         "已确认五列各五个格子的重复边线；" + "+".join(str(len(row)) for row in rows),
                         (width, height))
