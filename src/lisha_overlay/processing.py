"""CPU recognition and planning run off the GUI thread, one snapshot at a time."""
from dataclasses import replace
import json
import time
import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage

from .live_strategy import evaluate_current as evaluate, evaluate_exact, prepare_live_strategy
from .team_layout import TeamLayout
from .cached_analysis import AnalysisCache


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
        prepare_live_strategy()

    @Slot(QImage, dict, dict)
    def process(self, image, capture_metadata, config):
        started = time.perf_counter()
        metadata = {**capture_metadata, "generation": config["generation"],
                    'difficulty': config.get('difficulty', 'normal')}
        try:
            if QThread.currentThread().isInterruptionRequested():
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
            overrides = config.get("overrides", {})
            players = [replace(player, is_tank=False, **{key: value for key, value in overrides.get(player.player_key, {}).items()
                                          if key in {"color", "stacks", "alive", "isolated"}})
                       for player in players]
            evaluator = evaluate_exact if config.get('prefer_exact') else evaluate
            result = self._plan_cache.evaluate(players, config["mode"], snapshot_stage=config.get("snapshot_stage", "before_tank"),
                                               evaluator=evaluator, difficulty=config.get('difficulty', 'normal'))
            metadata["planning_cache_hit"] = self._plan_cache.last_hit
            confirmed = {p.player_key for p in players} if config.get("isolation_confirmed") else {
                key for key, value in overrides.items() if "isolated" in value}
            diagnostics = getattr(self._engine, "diagnostics", {})
            confirmed.update(key for key, item in diagnostics.items() if item.get("isolation_confirmed") is True)
            unconfirmed_isolation = [p.player_key for p in players if p.alive and p.player_key not in confirmed]
            reference_reasons = {}
            for player in players:
                if not player.alive:
                    continue
                detail = diagnostics.get(player.player_key, {})
                reasons = list(detail.get('estimate_reasons', []))
                if reasons:
                    reference_reasons[player.player_key] = reasons
            if reference_reasons and result.status in ('solved', 'no_solution'):
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
            metadata["reference_without_tank"] = False
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
