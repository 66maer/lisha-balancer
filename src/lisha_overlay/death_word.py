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
        config_path = directory / 'configuration.json'
        config = json.loads(config_path.read_text(encoding='utf8')) if config_path.is_file() else {}
        for filename in ('death_word_mask.png', 'death_word_video_mask.png'):
            path = directory / filename
            expected = config.get(f'{path.stem}_sha256')
            if expected is None and not path.is_file():
                continue
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('重伤字样参考图校验失败。')
            self.templates.append(cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_GRAYSCALE))
        for path in (Path(__file__).parent/'assets'/'buffs').glob('ui_dead*.png'):
            image=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR)
            ink=word_ink(image);ys,xs=np.where(ink)
            if len(xs)>=20:self.templates.append(ink[ys.min():ys.max()+1,xs.min():xs.max()+1])

    def match(self, roi, cell_height):
        best = self._best_score(word_ink(roi), cell_height)
        if best >= .88:
            return True, best
        # Small, resampled glyphs lose thin strokes when binarized. Use the
        # softer red contrast only on dark frames, where the death word sits.
        if (roi.max(axis=2) < 75).mean() < .65:
            return False, best
        b, g, r = cv2.split(roi[:, :, :3].astype(np.int16))
        contrast = np.uint8(np.clip((r - np.maximum(g, b) - 12) * 3, 0, 255))
        # Keep the smoothing radius proportional across different UI scales.
        if cell_height > 52:
            scale = 52 / cell_height
            height, width = contrast.shape
            size = max(1, round(width * scale)), max(1, round(height * scale))
            contrast = cv2.resize(contrast, size, interpolation=cv2.INTER_AREA)
        soft = self._best_score(contrast, min(cell_height, 52), soften=True)
        return soft > 0, max(best, soft)

    def _best_score(self, ink, cell_height, *, soften=False):
        if np.count_nonzero(ink) < 20:
            return 0.
        if soften:
            ink = cv2.GaussianBlur(ink, (3, 3), 1.3)
        best = 0.
        for index, reference in enumerate(self.templates):
            for height in range(7, min(40, round(cell_height * .5), ink.shape[0]) + 1):
                width = round(height * reference.shape[1] / reference.shape[0])
                if not 5 <= width <= ink.shape[1]:
                    continue
                key = index, width, height, soften
                template = self.scaled.get(key)
                if template is None:
                    interpolation = cv2.INTER_AREA if height < reference.shape[0] else cv2.INTER_LINEAR
                    template = cv2.resize(reference, (width, height), interpolation=interpolation)
                    if soften:
                        template = cv2.GaussianBlur(template, (3, 3), 1.3)
                    self.scaled[key] = template
                scores = cv2.matchTemplate(ink, template, cv2.TM_CCOEFF_NORMED)
                if not soften:
                    score = float(scores.max())
                    if np.isfinite(score):
                        best = max(best, score)
                    continue
                # A nearby red digit can fit half the smoothed word. Require
                # matching strokes on both sides of the same candidate.
                for y, x in np.argwhere(np.isfinite(scores) & (scores >= .86) & (scores > best)):
                    half = width // 2
                    patch = ink[y:y+height, x:x+width]
                    first = cv2.matchTemplate(patch[:, :half], template[:, :half], cv2.TM_CCOEFF_NORMED).item()
                    second = cv2.matchTemplate(patch[:, half:], template[:, half:], cv2.TM_CCOEFF_NORMED).item()
                    if first >= .60 and second >= .60:
                        best = max(best, float(scores[y, x]))
        return best
