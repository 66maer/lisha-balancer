"""Confirm complete snapshots, with bounded holds and explicit invalidation."""
from __future__ import annotations

import math


class SnapshotConfirmation:
    def __init__(self, required=2, maximum_hold_seconds=.3):
        if required < 1 or maximum_hold_seconds < 0:
            raise ValueError('Invalid snapshot confirmation policy')
        self.required = required
        self.maximum_hold_seconds = maximum_hold_seconds
        self.reset()

    def reset(self):
        self.confirmed = self.pending = None
        self.count = 0
        self.last_seen = self.last_input = None

    def update(self, signature, seconds, *, trustworthy=True):
        if not math.isfinite(seconds):
            self.reset()
            return 'unverified', None
        if self.last_input is not None and (seconds < self.last_input or seconds-self.last_input > .5):
            self.reset()
        duplicate = seconds == self.last_input
        self.last_input = seconds
        if not trustworthy or not signature:
            self.reset()
            return 'unverified', None
        if signature == self.confirmed:
            self.pending, self.count, self.last_seen = None, 0, seconds
            return 'confirmed', self.confirmed
        if not duplicate:
            self.count = self.count+1 if signature == self.pending else 1
            self.pending = signature
        if self.count >= self.required:
            self.confirmed, self.last_seen = signature, seconds
            self.pending, self.count = None, 0
            return 'confirmed', self.confirmed
        if self.confirmed is not None and seconds-self.last_seen <= self.maximum_hold_seconds+1e-8:
            return 'held', self.confirmed
        return 'confirming', None
