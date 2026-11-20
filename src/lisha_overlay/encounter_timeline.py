"""Estimate the live planning phase from timestamped team snapshots."""
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from .solver import Mode, Stage


@dataclass(frozen=True, slots=True)
class Phase:
    key: str
    label: str
    mode: Mode | None = None
    stage: Stage = 'after_tank'


WAITING = Phase('waiting', '等待团框出现首个Buff')
SYNCING = Phase('syncing', '等待团框清空后起表')
BEFORE_TANK = Phase('before_tank', '一式前', 'all', 'before_tank')
TANK_CAST = Phase('tank_cast', '一式结算中')
AFTER_TANK = Phase('after_tank', '一式后 · 等扇形', 'all')
FAN_CAST = Phase('fan_cast', '扇形结算中')
AFTER_FAN = Phase('after_fan', '扇形后 · 仅圈', 'circle')
CIRCLES = (
    Phase('circle_1', '一圈结算中'),
    Phase('circle_2', '二圈结算中'),
    Phase('circle_3', '三圈结算中'),
)
SETTLING = Phase('settling', '等待结算清空')

ONE_CAST = 41.8
ONE_HIT = 43.8
FAN_HIT = 46.7
CIRCLE_HITS = (61.2, 62.7, 64.3)
SETTLEMENT = 77.5
MARGIN = 1.0
MAX_FRAME_GAP = 3.0
ONSET_CONFIRMATION_WINDOW = 4.0
TimelineSample = Literal['clean', 'active', 'tentative']


class EncounterTimeline:
    def __init__(self):
        self.reset()

    def reset(self):
        self.started_at: float | None = None
        self.last_frame_at: float | None = None
        self.armed = False
        self._clean_frames = 0
        self._last_clean_at: float | None = None
        self._clean_since: float | None = None
        self._candidate_at: float | None = None
        self._active_frames = 0

    def observe(self, seconds: float, sample: TimelineSample | None) -> Phase:
        if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not isfinite(seconds):
            return SYNCING
        if self.last_frame_at is not None:
            gap = seconds - self.last_frame_at
            if gap < 0 or gap > MAX_FRAME_GAP:
                self.reset()
            elif gap == 0:
                return self.phase(seconds)
        self.last_frame_at = seconds

        self._clean_frames = self._clean_frames + 1 if sample == 'clean' else 0
        if sample == 'clean':
            self._last_clean_at = seconds
            if self._clean_since is None:
                self._clean_since = seconds
        else:
            self._clean_since = None
        if self.started_at is not None:
            if self._clean_frames >= 2 and (seconds - self.started_at >= SETTLEMENT - MARGIN
                                           or seconds - self._clean_since >= 2.0):
                self.started_at = None
                self.armed = True
            return self.phase(seconds)

        if self.armed:
            if self._candidate_at is None:
                self.armed = seconds - self._last_clean_at <= MAX_FRAME_GAP
            elif seconds - self._candidate_at > ONSET_CONFIRMATION_WINDOW:
                self.armed = False
                self._candidate_at = None
                self._active_frames = 0
        if sample in ('active', 'tentative') and self.armed:
            if self._candidate_at is None:
                self._candidate_at = seconds
            self._active_frames += int(sample == 'active')
            if self._active_frames >= 2:
                self.started_at = self._candidate_at
                self._candidate_at = None
                self._active_frames = 0
        elif sample == 'clean':
            self._candidate_at = None
            self._active_frames = 0
            if self._clean_frames >= 2:
                self.armed = True
        return self.phase(seconds)

    def phase(self, seconds: float) -> Phase:
        if self.started_at is None:
            return WAITING if self.armed else SYNCING
        elapsed = seconds - self.started_at
        if elapsed < ONE_CAST - MARGIN:
            return BEFORE_TANK
        if elapsed < ONE_HIT + MARGIN:
            return TANK_CAST
        if elapsed < FAN_HIT - MARGIN:
            return AFTER_TANK
        if elapsed < FAN_HIT + MARGIN:
            return FAN_CAST
        if elapsed < CIRCLE_HITS[0] - MARGIN:
            return AFTER_FAN
        for boundary, phase in zip((*CIRCLE_HITS[1:], CIRCLE_HITS[-1] + MARGIN), CIRCLES):
            if elapsed < boundary:
                return phase
        return SETTLING
