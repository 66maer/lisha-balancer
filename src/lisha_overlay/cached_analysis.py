"""Reuse a plan only for an identical complete roster and analysis context."""
from copy import deepcopy

from .analysis import evaluate_current as evaluate


class AnalysisCache:
    def __init__(self):
        self.reset()

    def reset(self):
        self._key=None;self._result=None;self.last_hit=False

    def evaluate(self,players,mode='all',*,snapshot_stage='before_tank',evaluator=None):
        snapshot=tuple(players)
        solve=evaluate if evaluator is None else evaluator
        key=(snapshot,mode,snapshot_stage,solve)
        self.last_hit=self._result is not None and key==self._key
        if not self.last_hit:
            result=solve(snapshot,mode,snapshot_stage=snapshot_stage)
            self._key=key;self._result=deepcopy(result)
        # Callers append per-frame warnings and diagnostics. Never let those
        # mutations leak into a later frame's otherwise identical plan.
        return deepcopy(self._result)
