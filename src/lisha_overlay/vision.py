"""CPU-only recognition of calibrated team panels with per-cell evidence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QRectF, QThread
from PySide6.QtGui import QImage

from .analysis import PlayerState
from .team_layout import PlayerCell, TeamLayout


def _read_image(path: Path) -> np.ndarray | None:
    if not path.is_file():
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def _resize(image: np.ndarray, width: int, height: int | None = None) -> np.ndarray:
    height = width if height is None else height
    interpolation = cv2.INTER_AREA if width < image.shape[1] else cv2.INTER_CUBIC
    return cv2.resize(image, (width, height), interpolation=interpolation)


def _qimage_bgr(frame: QImage) -> np.ndarray:
    if not isinstance(frame, QImage) or frame.isNull():
        raise ValueError("识别需要有效的截图。")
    converted = frame.convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(converted.constBits(), dtype=np.uint8,
                         count=converted.sizeInBytes()).reshape(converted.height(), converted.bytesPerLine())
    rgba = rows[:, :converted.width() * 4].reshape(converted.height(), converted.width(), 4)
    # cvtColor allocates a detached buffer; no Qt-owned bytes escape this call.
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)


def _pixel_rect(rect: QRectF, image: np.ndarray) -> tuple[int, int, int, int]:
    left = max(0, int(np.floor(rect.left())))
    top = max(0, int(np.floor(rect.top())))
    right = min(image.shape[1], int(np.ceil(rect.right())))
    bottom = min(image.shape[0], int(np.ceil(rect.bottom())))
    return left, top, max(left, right), max(top, bottom)


def _best_match(image: np.ndarray, template: np.ndarray,
                mask: np.ndarray | None = None, *,
                method: int = cv2.TM_SQDIFF_NORMED) -> tuple[float, tuple[int, int]]:
    if image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return 0.0, (0, 0)
    if mask is not None:
        x0, y0, width, height = cv2.boundingRect(mask)
        if not width or not height:
            return 0.0, (0, 0)
        x1, y1 = x0 + width, y0 + height
        # These masks are contiguous rectangles. Cropping both operands is
        # exactly equivalent and avoids OpenCV's much slower masked FFT path.
        image = image[y0:image.shape[0] - (template.shape[0] - y1),
                      x0:image.shape[1] - (template.shape[1] - x1)]
        template = template[y0:y1, x0:x1]
    scores = cv2.matchTemplate(image, template, method)
    minimum, maximum, location, maximum_location = cv2.minMaxLoc(scores)
    if method == cv2.TM_CCOEFF_NORMED:
        return max(0.0, min(1.0, maximum)) if math.isfinite(maximum) else 0.0, maximum_location
    return max(0.0, 1.0 - float(minimum)) if math.isfinite(minimum) else 0.0, location


class VisionEngine:
    """Analyze physical-pixel screenshots with the bundled CPU ONNX recognizer.

    ``diagnostics`` is replaced after each frame and contains per-slot evidence.
    Geometry, occlusions and visible death text are checked on every snapshot.
    """

    def __init__(self, layout: TeamLayout, tank_key: str | None = None, *,
                 backend: str | None = None, learned_assets=None):
        if not isinstance(layout, TeamLayout):
            raise TypeError("layout must be a TeamLayout.")
        if backend not in (None, "learned"):
            raise ValueError("Only the learned recognition backend is supported.")
        from .learned_recognition import LearnedRecognizer
        from .occlusion import CursorOcclusionDetector
        from .death_word import DeathWordDetector
        from .scene_words import SceneWordDetector

        self.layout = layout
        self.tank_key = tank_key
        self.backend = "learned"
        self.diagnostics: dict[str, dict[str, Any]] = {}
        self._learned_band_bounds = {}
        self._learned_icon_side = None
        self._learned_image_size = None
        self._dead_cache = {}
        root = Path(__file__).parent / "assets" / "buffs"
        self._dead_samples = [sample for path in sorted(root.glob("ui_dead*.png"))
                              if (sample := _read_image(path)) is not None]
        self._dead_glyphs = []
        for sample in self._dead_samples:
            ink = ((sample[:, :, 2] > 75) & (sample[:, :, 2] > sample[:, :, 1] * 1.4)
                   & (sample[:, :, 2] > sample[:, :, 0] * 1.3))
            ys, xs = np.where(ink)
            if xs.size:
                self._dead_glyphs.append(cv2.cvtColor(
                    sample[ys.min():ys.max()+1, xs.min():xs.max()+1], cv2.COLOR_BGR2GRAY))
        self._learned_recognizer = LearnedRecognizer(
            learned_assets, scale_offsets=(-1, 0, 1), cache_capacity=2048)
        self._cursor_occlusion = CursorOcclusionDetector(learned_assets)
        self._death_word = DeathWordDetector(learned_assets)
        self._scene_words = SceneWordDetector(learned_assets)

    def _dead_player(self, image: np.ndarray, cell: PlayerCell) -> tuple[bool, float]:
        if not self._dead_samples:
            return False, 0.0
        area = QRectF(cell.rect.x() + cell.rect.width() * 0.45,
                      cell.rect.y() + cell.rect.height() * 0.32,
                      cell.rect.width() * 0.60, cell.rect.height() * 0.70)
        left, top, right, bottom = _pixel_rect(area, image)
        roi = image[top:bottom, left:right]
        if not roi.size:
            return False, 0.0
        token = (left, top, right, bottom, roi.tobytes())
        cached = self._dead_cache.get(cell.key)
        if cached is not None and cached[0] == token:
            return cached[1]
        literal = self._death_word.match(roi, cell.rect.height())
        if literal[0]:
            self._dead_cache[cell.key] = (token, literal)
            return literal
        dark = (roi.max(axis=2) < 75).mean()
        red = ((roi[:, :, 2] > 75) & (roi[:, :, 2] > roi[:, :, 1] * 1.4)
               & (roi[:, :, 2] > roi[:, :, 0] * 1.3)).mean()
        if dark < 0.30 or red < 0.012:
            return False, 0.0
        confidence = 0.0
        expected = round(cell.rect.height() * 0.49)
        for sample in self._dead_samples:
            for height in set(range(12, 25)) | {expected - 1, expected, expected + 1}:
                if height <= 0:
                    continue
                width = round(height * sample.shape[1] / sample.shape[0])
                template = _resize(sample, width, height)
                score, _ = _best_match(roi, template)
                if score > confidence:
                    confidence = score
        glyph_confidence = 0.
        if confidence < .87:
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            for glyph in self._dead_glyphs:
                for height in range(7, min(22, roi.shape[0]) + 1):
                    width = round(height * glyph.shape[1] / glyph.shape[0])
                    template = _resize(glyph, width, height)
                    score, (x, y) = _best_match(gray, template, method=cv2.TM_CCOEFF_NORMED)
                    if score < .90:
                        continue
                    patch = roi[y:y+height, x:x+width]
                    red_ink = ((patch[:, :, 2] > 75) & (patch[:, :, 2] > patch[:, :, 1] * 1.4)
                               & (patch[:, :, 2] > patch[:, :, 0] * 1.3)).mean()
                    if red_ink >= .06:
                        glyph_confidence = max(glyph_confidence, score)
        result = confidence >= .87 or glyph_confidence >= .90, max(confidence, glyph_confidence)
        self._dead_cache[cell.key] = (token, result)
        return result

    def analyze(self, frame: QImage) -> list[PlayerState]:
        from .learned_rois import alignment_evidence
        from .learned_geometry import calibrated_bands
        from .occlusion import intersects
        image = _qimage_bgr(frame)
        if image.shape[:2] != self._learned_image_size:
            self._learned_band_bounds.clear();self._learned_icon_side=None;self._learned_image_size=image.shape[:2]
        cells = self.layout.cells(QRectF(frame.rect()))
        aligned = alignment_evidence(image, cells)
        crops, side_hint, scale_support, geometry = calibrated_bands(image, cells, return_evidence=True)
        if self._learned_icon_side is None and scale_support >= 3 and all(aligned.values()):
            self._learned_icon_side = side_hint
        self._learned_recognizer.fixed_icon_side = self._learned_icon_side or side_hint
        stable_crops = []
        for cell, (crop, bounds) in zip(cells, crops):
            if (cell.key not in self._learned_band_bounds and aligned[cell.key]
                    and geometry['left_support'].get(cell.group, 0) >= 2 and cell.key in geometry['row_keys']):
                self._learned_band_bounds[cell.key] = bounds
            if cell.key in self._learned_band_bounds:
                bounds = self._learned_band_bounds[cell.key]
                left, top, right, bottom = bounds
                crop = image[top:bottom, left:right, :3]
            stable_crops.append((crop, bounds))
        crops = stable_crops
        predictions = self._learned_recognizer.predict([crop for crop, bounds in crops])
        occlusions = self._cursor_occlusion.find(image,float(np.median([c.rect.height() for c in cells])))
        scene_words = self._scene_words.find(image,[bounds for crop,bounds in crops],float(np.median([c.rect.height() for c in cells])))
        # Preserve the existing death classifier exactly. Its independent
        # RGB searches release the GIL; sharing the small workload avoids a
        # long serial tail when changing health pixels invalidate its cache.
        workers = min(4, max(1, (os.cpu_count() or 2)//4))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='lisha-death') as pool:
            deaths = list(pool.map(lambda cell: self._dead_player(image, cell), cells))
        players, diagnostics = [], {}
        for cell, prediction, (_, bounds), (dead, death_score) in zip(cells, predictions, crops, deaths):
            if QThread.currentThread().isInterruptionRequested():
                raise InterruptedError("识别已停止。")
            state = prediction['state']
            color, stacks = ((state.split('_')[0], int(state[-1])) if state.startswith(('red_','black_'))
                             else ('unknown',None) if state=='unknown' else ('none', 0))
            source = "图标、赤幽字样及独立数字批量识别"
            advisory=[]
            if prediction.get('unverified_body'):advisory.append('字样与图标本体不符')
            if prediction.get('unverified_scale'):advisory.append('不同尺寸候选不一致')
            if prediction.get('unverified_isolation'):advisory.append('隔绝图案证据不足')
            if dead:
                color, stacks, source = 'none', 0, "已匹配重伤字形"
            elif not aligned[cell.key]:
                color, stacks, source = 'unknown', None, "团框结构或画面清晰度不足"
            isolated = prediction['isolated']
            occluded = any(intersects(bounds,box) for box in occlusions)
            if occluded:
                advisory.append('鼠标可能遮挡')
            scene_occluded = any(intersects(bounds,[r['bounds'][0],r['bounds'][1],r['bounds'][2]-r['bounds'][0],r['bounds'][3]-r['bounds'][1]]) for r in scene_words)
            if scene_occluded:
                advisory.append('游戏菜单可能覆盖团队数据')
            # Checks explain uncertainty without replacing a model argmax.
            # Missing geometry above is still a real recognition failure.
            diagnostics[cell.key] = {**prediction, 'backend': 'learned', 'source': source,
                'buff_band': bounds, 'death_confidence': float(death_score), 'localized': aligned[cell.key],
                'raw_state': state, 'raw_isolated': prediction['isolated'],
                'isolation_confirmed': prediction['isolation_confirmed'],
                'unverified_occlusion': occluded,
                'unverified_scene':scene_occluded,'scene_word_evidence':scene_words if scene_occluded else [],
                'neural_batch':dict(self._learned_recognizer.last_inference),
                'occlusion_rects': occlusions if occluded else [],
                'estimated': bool(advisory) and not dead, 'estimate_reasons': advisory if not dead else [],
                'warnings': advisory if not dead else []}
            players.append(PlayerState(cell.key, color, stacks, isolated=isolated,
                                       alive=not dead, is_tank=cell.key == self.tank_key))
        self.diagnostics = diagnostics
        return players
