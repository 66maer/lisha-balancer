"""Estimate the live planning phase from timestamped team snapshots."""
from collections.abc import Mapping
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
TANK_CAST = Phase('tank_cast', '结算一式打T')
AFTER_TANK = Phase('after_tank', '一式后 · 等扇形', 'all')
FAN_CAST = Phase('fan_cast', '结算二式扇形')
AFTER_FAN = Phase('after_fan', '扇形后 · 仅圈', 'circle')
CIRCLES = (
    Phase('circle_1', '结算一圈'),
    Phase('circle_2', '结算二圈'),
    Phase('circle_3', '结算三圈'),
)
SETTLING = Phase('settling', '结算统计')

ONE_CAST = 41.8
ONE_HIT = 43.8
FAN_HIT = 46.7
CIRCLE_HITS = (61.2, 62.7, 64.3)
# Estimate when the stacks disappear, starting from the first visible stacks.
SETTLEMENT = 82.0
MANUAL_CLEAR_DELAY = 3.0
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
        self._settlement_confirmed = False
        self._clean_frames = 0
        self._last_clean_at: float | None = None
        self._clean_since: float | None = None
        self._candidate_at: float | None = None
        self._active_frames = 0
        self._last_buffs: dict[str, int] = {}
        self._cleared_buffs: dict[str, int] | None = None
        self._new_holders: set[str] = set()

    @property
    def can_clear_round(self) -> bool:
        return (self.started_at is not None and self.last_frame_at is not None
                and self._clean_frames == 0
                and self.last_frame_at - self.started_at >= SETTLEMENT + MANUAL_CLEAR_DELAY)

    def clear_round(self) -> None:
        """Keep the last readable stacks so an old icon cannot immediately start a new round."""
        baseline = self._last_buffs.copy()
        self.reset()
        self._cleared_buffs = baseline

    def observe(self, seconds: float, sample: TimelineSample | None,
                buffs: Mapping[str, int] | None = None) -> Phase:
        if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not isfinite(seconds):
            return SYNCING
        if self.last_frame_at is not None:
            gap = seconds - self.last_frame_at
            if gap < 0:
                self.reset()
            elif gap > MAX_FRAME_GAP:
                if self._cleared_buffs is None and self.phase(self.last_frame_at) != SETTLING:
                    self.reset()
                else:
                    self._clean_frames = 0
                    self._clean_since = None
                    self._new_holders.clear()
            elif gap == 0:
                return self.phase(seconds)
        self.last_frame_at = seconds
        if buffs is not None:
            self._last_buffs.update(buffs)

        self._clean_frames = self._clean_frames + 1 if sample == 'clean' else 0
        if sample == 'clean':
            self._last_clean_at = seconds
            if self._clean_since is None:
                self._clean_since = seconds
        else:
            self._clean_since = None
        if self._cleared_buffs is not None:
            if self._clean_frames >= 2:
                self._cleared_buffs = None
                self.armed = self._settlement_confirmed = True
                self._candidate_at = None
            else:
                new_holders = set()
                for key, value in (buffs or {}).items():
                    previous = self._cleared_buffs.get(key)
                    if value and (previous is None or value * previous <= 0 or abs(value) > abs(previous)):
                        new_holders.add(key)
                if new_holders & self._new_holders:
                    self.started_at = self._candidate_at
                    self._cleared_buffs = None
                    self._candidate_at = None
                else:
                    self._candidate_at = seconds if new_holders else None
                self._new_holders = new_holders
            return self.phase(seconds)
        if self.started_at is not None:
            if self._clean_frames >= 2 and (self.phase(seconds) == SETTLING
                                           or seconds - self._clean_since >= 2.0):
                self.started_at = None
                self.armed = self._settlement_confirmed = True
            return self.phase(seconds)

        if self.armed:
            if self._candidate_at is None and not self._settlement_confirmed:
                self.armed = seconds - self._last_clean_at <= MAX_FRAME_GAP
            elif self._candidate_at is not None and seconds - self._candidate_at > ONSET_CONFIRMATION_WINDOW:
                self.armed = self._settlement_confirmed
                self._candidate_at = None
                self._active_frames = 0
        if sample in ('active', 'tentative') and self.armed:
            if self._candidate_at is None:
                self._candidate_at = seconds
            self._active_frames += int(sample == 'active')
            if self._active_frames >= 2:
                self.started_at = self._candidate_at
                self._settlement_confirmed = False
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
            return WAITING if self.armed or self._cleared_buffs is not None else SYNCING
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
