"""CPU recognition and planning run off the GUI thread, one snapshot at a time."""
from copy import deepcopy
from dataclasses import replace
import json
import time
import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage

from .live_strategy import evaluate_simple_commands as evaluate, evaluate_exact_commands as evaluate_exact
from .team_layout import TeamLayout
from .cached_analysis import AnalysisCache
from .analysis import ACTIONABLE_STATUSES, AnalysisResult, count_buffs
from .difficulty import SAFE_TOLERANCE
from .encounter_timeline import EncounterTimeline, TimelineSample
from .solver import BuffState, simulate_plan


def _player_labels(keys):
    labels = []
    for key in keys:
        text = str(key)
        group, separator, slot = text[1:].partition("p")
        if text.startswith("g") and separator and group.isdecimal() and slot.isdecimal():
            labels.append(f"{int(group)}队{int(slot)}")
        else:
            labels.append(text)
    return "、".join(labels)


def _timeline_evidence(players, diagnostics) -> tuple[TimelineSample | None, dict[str, int]]:
    """Only red and black stacks drive the encounter clock."""
    if len(players) != 25 or len({p.player_key for p in players}) != 25 or any(
        not diagnostics.get(p.player_key, {}).get('localized') for p in players
    ):
        return None, {}
    buffs = {}
    tentative = False
    for player in players:
        detail = diagnostics[player.player_key]
        if detail.get('unverified_scene') or detail.get('unverified_occlusion'):
            continue
        if not player.alive:
            buffs[player.player_key] = 0
            continue
        layers = player.stacks
        active = player.color in ('red', 'black') and type(layers) is int and 1 <= layers <= 5
        if detail.get('unverified_body') or detail.get('unverified_buff_scale', detail.get('unverified_scale')):
            tentative |= active
            continue
        if active:
            buffs[player.player_key] = -layers if player.color == 'black' else layers
        elif player.color == 'none' and (layers is None or (type(layers) is int and layers == 0)):
            buffs[player.player_key] = 0
    if any(buffs.values()):
        return 'active', buffs
    if tentative:
        return 'tentative', buffs
    return ('clean' if len(buffs) == len(players) else None), buffs


def _observed_buffs(players, diagnostics):
    if len(players) != 25 or len({p.player_key for p in players}) != 25:
        return None
    states = {}
    for player in players:
        flags = diagnostics.get(player.player_key, {})
        if not flags.get('localized') or flags.get('unverified_scene'):
            return None
        if not player.alive:
            states[player.player_key] = BuffState(0, False, False)
            continue
        layers = player.stacks
        if player.color == 'none':
            valid_layers = layers is None or (type(layers) is int and layers == 0)
        else:
            valid_layers = player.color in ('red', 'black') and type(layers) is int and 1 <= layers <= 5
        if flags.get('unverified_occlusion') or not valid_layers:
            return None
        value = (layers or 0) * (-1 if player.color == 'black' else 1)
        states[player.player_key] = BuffState(value, player.isolated)
    return states


def _update_observed_counts(result, players):
    red, black, counts = count_buffs(players)
    delta = red - black
    result.current_red, result.current_black, result.buff_counts = red, black, counts
    result.summary = f'红 {red}  黑 {black}  差 {delta:+d}' if delta else f'红 {red}  黑 {black}  差 0'


class AnalysisWorker(QObject):
    # Preserve Python dataclasses across the queued connection without
    # converting them through QVariantList/Map containers.
    completed = Signal(object, object, object)
    failed = Signal(str, dict)

    def __init__(self, *, backend=None):
        super().__init__()
        self._engine = None
        self._signature = None
        self._plan_cache = AnalysisCache()
        self._backend = backend
        self._timeline = EncounterTimeline()
        self._timeline_session = None
        self._round_context = None
        self._round_result = None
        self._round_states = {}
        self._round_mismatch = None
        self._xinfa_reader = None
        self._xinfa_scan = None
        self._xinfa_request = None

    def _sample_xinfa(self, image, metadata, request, layout):
        try:
            from .xinfa_recognition import RosterReader, RosterScan
            if self._xinfa_reader is None:
                self._xinfa_reader = RosterReader()
            if request != self._xinfa_request or self._xinfa_scan is None:
                self._xinfa_request = request
                self._xinfa_scan = RosterScan()
            seconds = metadata.get('captured_at') or time.time()
            draft = self._xinfa_scan.observe(self._xinfa_reader, image, layout, seconds)
            if draft is not None:
                metadata.update(xinfa_request=request, xinfa_draft=draft, xinfa_candidates=self._xinfa_scan.candidates)
        except Exception as error:
            logging.getLogger(__name__).exception('Roster recognition failed')
            metadata.update(xinfa_request=request, xinfa_error=str(error))

    def _new_engine(self, layout, tank_key):
        from .vision import VisionEngine
        if self._backend is not None:
            return VisionEngine(layout, tank_key, backend=self._backend)
        return VisionEngine(layout, tank_key)

    def prepare(self, layout, tank_key=None):
        """Load image libraries and fixed templates before the start action."""
        import cv2
        cv2.setNumThreads(1)
        self._engine = self._new_engine(TeamLayout.from_dict(layout), tank_key)
        self._signature = json.dumps([layout, tank_key], sort_keys=True)
        self._plan_cache.reset()

    def _auto_result(self, phase, players, diagnostics, config, metadata):
        """Keep one verified set of assignments until a stable checkpoint disagrees."""
        context = self._timeline_session, self._timeline.started_at, config['generation']
        if context != self._round_context:
            self._round_context = context
            self._round_result = None
            self._round_states = {}
            self._round_mismatch = None
        observed = _observed_buffs(players, diagnostics)
        if self._round_result is not None:
            planned = {action.player_key for action in self._round_result.plan.actions
                       if action.circles or (phase.mode != 'circle' and action.fan)}
            if phase.mode == 'all':
                planned.update(player.player_key for player in players if player.is_tank)
            lost_player = phase.mode is not None and any(
                not player.alive and player.player_key in planned
                and diagnostics.get(player.player_key, {}).get('localized')
                and not diagnostics[player.player_key].get('unverified_scene')
                for player in players
            )
            expected = self._round_states.get(phase.key)
            changed = ()
            if expected is not None and observed is not None and expected.keys() == observed.keys():
                changed = tuple((key, value) for key, value in sorted(observed.items())
                                if value != expected[key] and isinstance(value.isolated, bool)
                                and not diagnostics[key].get('estimate_reasons'))
            instant = metadata.get('captured_at')
            key = phase.key, changed
            failed = bool(changed and self._round_mismatch is not None
                          and self._round_mismatch[0] == key and self._round_mismatch[1] != instant)
            self._round_mismatch = (key, instant) if changed else None
            if not failed and not lost_player:
                if observed is not None:
                    _update_observed_counts(self._round_result, players)
                held = deepcopy(self._round_result)
                held.reference |= observed is None or bool(changed)
                metadata['timeline_plan_held'] = True
                return held
            self._round_result = None
            self._round_mismatch = None
        if phase.mode is None:
            result = AnalysisResult(phase.label, status='timeline_wait',
                                    details='按画面统计当前煞气，清空后自动开始下一轮。')
            if self._timeline.started_at is not None and observed is not None:
                _update_observed_counts(result, players)
            return result
        result = self._evaluate(players, phase.mode, phase.stage, config, metadata)
        if result.plan is not None and observed is not None:
            trace = simulate_plan(players, result.plan, tolerance=SAFE_TOLERANCE[metadata['difficulty']],
                                  trace_stages=True)
            if trace.valid:
                self._round_result = deepcopy(result)
                self._round_states = trace.stage_states
        return result

    def _evaluate(self, players, mode, stage, config, metadata):
        evaluator = evaluate_exact if config.get('prefer_exact') else evaluate
        result = self._plan_cache.evaluate(players, mode, snapshot_stage=stage, evaluator=evaluator,
                                           difficulty=metadata['difficulty'])
        metadata['planning_cache_hit'] = self._plan_cache.last_hit
        return result

    @Slot(QImage, dict, dict)
    def process(self, image, capture_metadata, config):
        started = time.perf_counter()
        metadata = {**capture_metadata, "generation": config["generation"],
                    'difficulty': config.get('difficulty', 'normal')}
        try:
            if QThread.currentThread().isInterruptionRequested():
                return
            if config.get('xinfa_only'):
                metadata['xinfa_only'] = True
                self._sample_xinfa(image, metadata, config['xinfa_request'], TeamLayout.from_dict(config['layout']))
                self.completed.emit(None, [], metadata)
                return
            import cv2
            cv2.setNumThreads(1)
            signature = json.dumps([config["layout"], config.get("tank_key")], sort_keys=True)
            if signature != self._signature:
                self._engine = self._new_engine(TeamLayout.from_dict(config["layout"]), config.get("tank_key"))
                self._signature = signature
                self._plan_cache.reset()
            vision_started = time.perf_counter()
            metadata["initialization_ms"] = (vision_started - started) * 1000
            players = self._engine.analyze(image)
            metadata["vision_backend"] = getattr(self._engine, "backend", "learned")
            metadata["recognition_ms"] = (time.perf_counter() - vision_started) * 1000
            planning_started = time.perf_counter()
            if QThread.currentThread().isInterruptionRequested():
                return
            request = config.get('xinfa_request')
            if request is not None:
                self._sample_xinfa(image, metadata, request, self._engine.layout)
            diagnostics = getattr(self._engine, "diagnostics", {})
            auto = config.get('auto_timeline')
            sample, buffs = _timeline_evidence(players, diagnostics) if auto else (None, {})
            overrides = config.get("overrides", {})
            roster = config.get('xinfa_roster', {})
            players = [replace(player, is_tank=player.player_key == config.get('tank_key'),
                               xinfa=roster.get(player.player_key),
                               **{key: value for key, value in overrides.get(player.player_key, {}).items()
                                          if key in {"color", "stacks", "alive", "isolated"}})
                       for player in players]
            mode = config['mode']
            stage = config.get('snapshot_stage', 'after_tank') if mode != 'circle' else 'after_tank'
            phase = None
            if auto:
                session = (config.get('timeline_session'), config['layout'])
                if session != self._timeline_session:
                    clear_from = config.get('timeline_clear_from')
                    if clear_from is not None and self._timeline_session == (clear_from, config['layout']):
                        self._timeline.clear_round()
                    else:
                        self._timeline.reset()
                    self._timeline_session = session
                phase = self._timeline.observe(metadata.get('captured_at'), sample, buffs)
                mode, stage = phase.mode, phase.stage
                metadata.update(timeline_phase=phase.key, timeline_label=phase.label,
                                timeline_started_at=self._timeline.started_at,
                                timeline_can_clear=self._timeline.can_clear_round)
            metadata.update(effective_mode=mode, snapshot_stage=stage)
            metadata['planning_cache_hit'] = False
            result = (self._auto_result(phase, players, diagnostics, config, metadata) if auto
                      else self._evaluate(players, mode, stage, config, metadata))
            confirmed = {p.player_key for p in players} if config.get("isolation_confirmed") else {
                key for key, value in overrides.items() if "isolated" in value}
            confirmed.update(key for key, item in diagnostics.items() if item.get("isolation_confirmed") is True)
            unconfirmed_isolation = [p.player_key for p in players if p.alive and p.player_key not in confirmed]
            reference_reasons = {}
            for player in players:
                if not player.alive or (result.status == 'tank_warning' and not player.is_tank):
                    continue
                detail = diagnostics.get(player.player_key, {})
                reasons = list(detail.get('estimate_reasons', []))
                if reasons:
                    reference_reasons[player.player_key] = reasons
            if reference_reasons and (
                result.status in ACTIONABLE_STATUSES or (result.status == 'timeline_wait' and result.buff_counts is not None)
            ):
                result.reference = True
                note = '按模型候选计算：' + _player_labels(reference_reasons)
                result.warnings.append(note)
                evidence=[]
                for player in players:
                    if player.player_key not in reference_reasons:continue
                    detail=diagnostics[player.player_key]
                    label=('赤' if player.color=='red' else '幽')+str(player.stacks) if player.color in ('red','black') else '无'
                    if player.isolated:label+='／隔绝'
                    evidence.append(f"{_player_labels([player.player_key])}={label}（模型分值{detail.get('confidence',0.):.3f}；"
                                    +'、'.join(reference_reasons[player.player_key])+'）')
                result.details = note+'。\n'+'\n'.join(evidence)+'\n'+result.details
            metadata['reference_keys'] = list(reference_reasons)
            metadata['reference_reasons'] = reference_reasons
            metadata['model_output_policy'] = 'best_current_prediction'
            metadata["diagnostics"] = diagnostics
            metadata["isolation_unconfirmed_keys"] = unconfirmed_isolation
            metadata["reference_without_tank"] = bool(mode in ('all', 'fan') and stage == 'before_tank'
                                                       and not config.get('tank_key'))
            metadata["warnings"] = list(result.warnings)
            metadata["processing_ms"] = (time.perf_counter() - started) * 1000
            metadata["planning_ms"] = (time.perf_counter() - planning_started) * 1000
            metadata["manual_keys"] = list(overrides)
            metadata["unknown_keys"] = [p.player_key for p in players if p.alive and (
                p.color == "unknown" or (p.color in ("red", "black") and p.stacks is None))]
            metadata['unverified_isolation_keys'] = [p.player_key for p in players if p.isolated is None]
            if result.status == "needs_recognition":
                count = len(metadata["unknown_keys"])
                result.summary = f"Buff待识别 · {count}人" if count else "Buff待识别"
                unknown_types = [p.player_key for p in players if p.alive and p.color == "unknown"]
                unknown_stacks = [p.player_key for p in players if p.alive and (
                    p.color in ("red", "black") and p.stacks is None)]
                details = []
                if unknown_types:
                    details.append("Buff 类型待确认：" + _player_labels(unknown_types))
                if unknown_stacks:
                    details.append("Buff 层数待确认：" + _player_labels(unknown_stacks))
                if details:
                    details.append("可在设置预览中核对这些人员的 Buff。")
                    result.details = "\n".join(details)
                elif not result.details:
                    result.details = "Buff 状态尚未确认，可在设置预览中核对。"
                if metadata['unverified_isolation_keys']:
                    result.summary = f"隔绝待确认 · {len(metadata['unverified_isolation_keys'])}人"
                    result.details = '图标与隔绝字样证据冲突：'+_player_labels(metadata['unverified_isolation_keys'])+'。暂停标记，等待下一帧确认。'
                occluded=[key for key,d in diagnostics.items() if d.get('unverified_occlusion')]
                if occluded:
                    result.summary=f'画面被遮挡 · {len(occluded)}人'
                    result.details='鼠标遮挡 Buff：'+_player_labels(occluded)+'。暂停标记，等待画面恢复。'
                scale_pending=[key for key,d in diagnostics.items() if d.get('unverified_scale')]
                if scale_pending and not occluded:
                    result.summary=f'Buff待确认 · {len(scale_pending)}人'
                    result.details='图标边框的识别结果不一致：'+_player_labels(scale_pending)+'。暂停标记，等待下一帧。'
                body_pending=[p.player_key for p in players if p.alive and diagnostics.get(p.player_key,{}).get('unverified_body')]
                if body_pending and not occluded:
                    result.summary=f'画面待确认 · {len(body_pending)}人'
                    result.details='字样与图标本体不符：'+_player_labels(body_pending)+'。暂停标记，等待画面恢复。'
                scene_pending=[p.player_key for p in players if p.alive and diagnostics.get(p.player_key,{}).get('unverified_scene')]
                if scene_pending:
                    result.summary='面板被遮挡'
                    result.details='游戏菜单覆盖团队数据，暂停标记，等待画面恢复。'
            self.completed.emit(result, players, metadata)
        except Exception as error:
            logging.getLogger(__name__).exception('Recognition/planning worker failed')
            self.failed.emit(str(error), metadata)
