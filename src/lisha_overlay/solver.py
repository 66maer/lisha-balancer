"""Finite, verified action search for one Lisha correction sequence.

The positioning model has three distinct single-hit centers. A helper stands
in one circle, the intersection of circles 1 and 2, or all three circles.
For a three-circle helper, centers form a triangle around a common overlap,
while keeping each center outside the other circles. No player explosion or
center that also takes other circles is used as a correction tool.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Sequence

if TYPE_CHECKING:
    from .analysis import PlayerState

Mode = Literal["all", "fan", "circle"]
Stage = Literal["before_tank", "after_tank"]


@dataclass(frozen=True, slots=True)
class BuffState:
    # Positive = red, negative = black, zero = no buff.
    value: int
    isolated: bool = False
    alive: bool = True


@dataclass(frozen=True, slots=True)
class BuffEvent:
    state: BuffState
    applied: bool
    weak: int = 0
    strong: int = 0


class IllegalAction(ValueError):
    pass


def receive_buff(state: BuffState, color: str, amount: int = 1) -> BuffEvent:
    """Apply one game event, including one simultaneous +3 tank event.

    An isolated target cannot obtain any new buff. Opposing stacks annihilate
    before the remaining stack is assessed; zero remainder causes no extra
    cancellation damage, one causes weak damage, >=2 causes strong/isolation.
    """
    if color not in ("red", "black") or isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
        raise ValueError("Invalid buff event")
    if not state.alive:
        raise IllegalAction("重伤玩家不能参与后续操作。")
    if abs(state.value) >= 5:
        raise IllegalAction("已有五层煞气，不能作为安全配平状态。")
    if state.isolated:
        return BuffEvent(state, applied=False)
    delta = amount if color == "red" else -amount
    opposed = bool(state.value and state.value * delta < 0)
    value = state.value + delta
    if abs(value) >= 5:
        raise IllegalAction("获得煞气将达到五层并触发自爆。")
    if opposed and abs(value) >= 2:
        return BuffEvent(BuffState(value, True), applied=True, strong=1)
    return BuffEvent(BuffState(value), applied=True, weak=int(opposed and abs(value) == 1))


@dataclass(frozen=True, slots=True)
class PlayerAction:
    player_key: str
    fan: bool
    circles: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class SolverPlan:
    mode: Mode
    snapshot_stage: Stage
    actions: tuple[PlayerAction, ...]
    centers: tuple[str, str, str]
    reference_without_tank: bool = False
    current_snapshot: bool = False
    command_style: bool = False
    command_baseline: tuple[PlayerAction, ...] = ()
    spoken_policy: bool = False


@dataclass(slots=True)
class SimulationResult:
    valid: bool
    reason: str = ""
    final_states: dict[str, BuffState] = field(default_factory=dict)
    red_count: int = 0
    black_count: int = 0
    weak_cancellations: int = 0
    strong_cancellations: int = 0

    @property
    def difference(self) -> int:
        return abs(self.red_count - self.black_count)


@dataclass(slots=True)
class SearchResult:
    plan: SolverPlan | None
    simulation: SimulationResult | None
    reason: str = ""
    explored: int = 0


def _states(players: Sequence[PlayerState]) -> dict[str, BuffState]:
    result = {}
    for player in players:
        value = 0 if not player.alive or player.color == "none" else int(player.stacks or 0)
        if player.color == "black":
            value = -value
        if player.alive and abs(value) >= 5:
            raise IllegalAction("已有玩家达到五层，当前模型不采用抢消或自爆救场。")
        result[player.player_key] = BuffState(value, player.isolated, player.alive)
    return result


def simulate_plan(players: Sequence[PlayerState], plan: SolverPlan,
                  *, require_weak: bool = True, tolerance: int = 2) -> SimulationResult:
    """Independently replay the full stage sequence and verify every instruction."""
    try:
        if plan.mode not in ("all", "fan", "circle") or plan.snapshot_stage not in ("before_tank", "after_tank"):
            raise IllegalAction("技能阶段或工具模式无效。")
        states = _states(players)
        tanks = [] if plan.current_snapshot else [player for player in players if player.is_tank]
        live_tanks = [player for player in tanks if player.alive]
        if len(tanks) > 1 or len(states) != len(players):
            raise IllegalAction("不能指定多名坦克，玩家编号不能重复。")
        if plan.current_snapshot:
            tank_key = None
        elif plan.reference_without_tank:
            if live_tanks:
                raise IllegalAction("无 T 参考方案不能代替已指定存活 T 的完整结算。")
            tank_key = None
        else:
            if len(live_tanks) != 1:
                raise IllegalAction("必须有且仅有一名存活坦克。")
            tank_key = live_tanks[0].player_key
        weak = strong = 0
        if plan.snapshot_stage == "before_tank" and tank_key is not None:
            event = receive_buff(states[tank_key], "red", 3)
            states[tank_key] = event.state
            weak += event.weak
            strong += event.strong

        initially_isolated = {key for key, state in states.items() if state.isolated}
        actions = {action.player_key: action for action in plan.actions}
        if len(actions) != len(plan.actions) or set(actions) - set(states):
            raise IllegalAction("指挥名单包含重复或未知玩家。")
        for action in plan.actions:
            if not states[action.player_key].alive:
                raise IllegalAction("不能给重伤玩家分配扇形或黑圈。")
            if tuple(sorted(set(action.circles))) != action.circles:
                raise IllegalAction("同一个黑圈不能重复分配给同一玩家。")
            allowed = (((), (1,), (2,), (3,), (1, 3), (1, 2, 3)) if plan.spoken_policy else
                       ((), (1,), (2,), (3,), (1, 3)) if plan.command_style else
                       ((), (1,), (2,), (3,), (1, 2), (1, 2, 3)))
            if action.circles not in allowed:
                raise IllegalAction("只允许单圈，连续一二号圈，或连续三个圈的交集。")
            if action.fan:
                if plan.mode == "circle" or action.player_key == tank_key:
                    raise IllegalAction("当前模式或承伤坦克不能分配背后扇形。")
                event = receive_buff(states[action.player_key], "red")
                if not event.applied:
                    raise IllegalAction("隔绝玩家不能作为扇形修正对象。")
                states[action.player_key] = event.state
                weak += event.weak
                strong += event.strong

        if len(plan.centers) != 3 or len(set(plan.centers)) != 3:
            raise IllegalAction("幽影斩必须安排三名不同的点名中心。")
        for order, key in enumerate(plan.centers, 1):
            if key not in actions or actions[key].circles != (order,):
                raise IllegalAction("每个点名中心必须只承接其自己的那一个圈。")
        if plan.mode == "fan":
            # The mode's black hits are mandatory neutral carriers, not an
            # unadvertised circle-based correction hiding under the fan button.
            if any(action.circles and action.player_key not in plan.centers for action in plan.actions):
                raise IllegalAction("仅扇模式不分配贴圈修正。")
            if any(not states[key].isolated and not -3 <= states[key].value <= -1 for key in plan.centers):
                raise IllegalAction("仅扇模式的排圈者必须是隔绝者，或可安全加黑的黑一至三层。")

        for order in (1, 2, 3):
            for action in plan.actions:
                if order not in action.circles:
                    continue
                event = receive_buff(states[action.player_key], "black")
                if not event.applied:
                    # 幽影斩 still targets/damages an isolated player; unlike
                    # 天坠, a blocked buff here has no raid-wide miss explosion.
                    # Only a neutral single-hit center may use this property.
                    generated_isolation = plan.spoken_policy and action.player_key not in initially_isolated
                    if not generated_isolation and (action.player_key not in plan.centers or action.circles != (order,)):
                        raise IllegalAction("隔绝者可作中性点名中心，不能作为有效贴圈修正对象。")
                states[action.player_key] = event.state
                weak += event.weak
                strong += event.strong

        red = sum(state.alive and state.value > 0 for state in states.values())
        black = sum(state.alive and state.value < 0 for state in states.values())
        if require_weak and abs(red - black) > tolerance:
            raise IllegalAction(f"最终赤幽持有人数差仍超过{tolerance}，未达到安全结算。")
        return SimulationResult(True, final_states=states, red_count=red, black_count=black,
                                weak_cancellations=weak, strong_cancellations=strong)
    except (IllegalAction, KeyError) as error:
        return SimulationResult(False, reason=str(error))


@dataclass(frozen=True, slots=True)
class _Option:
    fan: bool
    hits: int
    state: BuffState
    # Remaining holders, newly created holders, strong damage, added layers,
    # remaining layers, repeated circles, instructions, events, weak damage,
    # fan instructions. All are additive for safe DP dominance.
    cost: tuple[int, ...]
    center_preference: tuple[int, int, int]
    forced_center: bool = False


def _options(state: BuffState, *, tank: bool, mode: Mode, current_policy: bool = False,
             preferred_action: tuple[bool,int] | None = None,
             avoid_overlap: bool = False) -> tuple[_Option, ...]:
    if not state.alive:
        cost_size = 10 + int(current_policy and avoid_overlap) + int(preferred_action is not None)
        return (_Option(False, 0, state, (0,) * cost_size, (1, 1, 1)),)
    options = {}
    for fan in (False, True) if mode != "circle" and not tank else (False,):
        try:
            after_fan = receive_buff(state, "red") if fan else BuffEvent(state, True)
            if not after_fan.applied:
                continue
        except IllegalAction:
            continue
        for hits in (0, 1, 2, 3) if mode != "fan" else (0, 1):
            if hits and mode == "fan" and not (
                after_fan.state.isolated or -3 <= after_fan.state.value <= -1
            ):
                continue
            current = after_fan.state
            added = max(0, abs(current.value) - abs(state.value))
            weak, strong = after_fan.weak, after_fan.strong
            forced_center = False
            try:
                for _ in range(hits):
                    event = receive_buff(current, "black")
                    added += max(0, abs(event.state.value) - abs(current.value))
                    if not event.applied:
                        if hits != 1:
                            raise IllegalAction("隔绝不能作为双圈修正对象。")
                        forced_center = True
                    current = event.state
                    weak += event.weak
                    strong += event.strong
            except IllegalAction:
                continue
            cost = (int(current.value != 0), int(state.value == 0 and current.value != 0),
                    strong, added, abs(current.value), max(0, hits - 1),
                    int(fan) + int(hits > 0), int(fan) + hits, weak, int(fan))
            if current_policy:
                # Existing same-colour stacks are harmless. Correct headcount
                # with few instructions, preferring removal over new holders;
                # do not add optional actions merely to clear more Buffs.
                cost = (strong, cost[1], cost[6], cost[5], cost[7], cost[0],
                        cost[4], cost[8], cost[9], cost[3])
                if avoid_overlap:
                    cost = (cost[0], int(hits == 3), int(hits == 2), *cost[1:3], *cost[4:])
            if preferred_action is not None:
                cost = (int((fan,hits)!=preferred_action),) + cost
            preference = (int(after_fan.state.value >= 0),
                          int(abs(after_fan.state.value) > 2), int(tank))
            option = _Option(fan, hits, current, cost, preference, forced_center)
            delta = int(current.value > 0) - int(current.value < 0)
            key = (delta, int(hits == 1), forced_center)
            previous = options.get(key)
            if previous is None or (cost, preference) < (previous.cost, previous.center_preference):
                options[key] = option
    return tuple(options.values())


def solve(players: Sequence[PlayerState], mode: Mode = "all", *,
          snapshot_stage: Stage = "before_tank", allow_missing_tank: bool = False,
          current_snapshot: bool = False, preferred_actions: dict[str,tuple[bool,int]] | None = None,
          fixed_centers: tuple[str,str,str] | None = None,
          tolerance: int = 2, avoid_overlap: bool = False) -> SearchResult:
    """Find a weak-settlement plan in O(players × difference × 4 × options).

    The DP tracks final red-minus-black count and up to three available
    single-hit circle centers. Each player's full legal two-stage action is
    evaluated before being combined with other players, so isolation is never
    treated as a reversible intermediate state. Explicitly allowing a missing
    tank searches only the current buff snapshot: no unknown +3 event is added
    and no player is assumed to be exempt from the fan.
    """
    if mode not in ("all", "fan", "circle"):
        raise ValueError("Unknown analysis mode")
    if snapshot_stage not in ("before_tank", "after_tank"):
        raise ValueError("Unknown snapshot stage")
    # Canonical column/slot order also makes equal-cost choices independent of
    # the caller's input ordering. Earlier columns win the final tie-break.
    players = sorted(players, key=lambda p: tuple(map(int, p.player_key[1:].split('p'))))
    if current_snapshot:
        snapshot_stage = 'after_tank'
    tanks = [] if current_snapshot else [player for player in players if player.is_tank]
    live_tanks = [player for player in tanks if player.alive]
    if len(tanks) > 1:
        return SearchResult(None, None, "不能同时指定多名坦克。")
    if not live_tanks and not (allow_missing_tank or current_snapshot):
        return SearchResult(None, None, "必须指定一名存活坦克。")
    tank_key = live_tanks[0].player_key if live_tanks else None
    try:
        initial = _states(players)
        if snapshot_stage == "before_tank" and tank_key is not None:
            initial[tank_key] = receive_buff(initial[tank_key], "red", 3).state
    except IllegalAction as error:
        return SearchResult(None, None, "一式前状态无法安全推进：" + str(error))

    zero = (0,) * (11 + int(current_snapshot and avoid_overlap) + int(preferred_actions is not None))
    # Neutral isolated carriers must occupy a center rather than a helper slot.
    # Track their number separately so cap(eligible, 3) cannot hide a fourth one.
    # (difference, eligible-centers, forced-centers): (cost, player-options)
    dp = {(0, 0, 0): (zero, ())}
    explored = 0
    for index, player in enumerate(players):
        options = _options(initial[player.player_key], tank=player.player_key == tank_key,
                           mode=mode, current_policy=current_snapshot,
                           preferred_action=preferred_actions.get(player.player_key,(False,0)) if preferred_actions is not None else None,
                           avoid_overlap=avoid_overlap)
        if preferred_actions is not None:
            options=tuple(o for o in options if o.hits<=2)
        if fixed_centers is not None:
            options=(tuple(o for o in options if not o.fan and o.hits==1) if player.player_key in fixed_centers
                     else tuple(o for o in options if not o.forced_center))
        updated = {}
        for (difference, center_count, forced_count), (cost, path) in dp.items():
            for option in options:
                explored += 1
                delta = int(option.state.alive and option.state.value > 0) - int(
                    option.state.alive and option.state.value < 0)
                next_forced = forced_count + int(option.forced_center)
                if next_forced > 3:
                    continue
                key = (difference + delta, min(3, center_count + int(option.hits == 1)), next_forced)
                position = -(1 << (len(players)-1-index)) if option.fan or option.hits else 0
                candidate_cost = tuple(a + b for a, b in zip(cost, option.cost + (position,)))
                previous = updated.get(key)
                if previous is None or candidate_cost < previous[0]:
                    updated[key] = (candidate_cost, path + (option,))
        dp = updated
        if not dp:
            return SearchResult(None, None, "没有不会触发五层或隔绝冲突的合法动作。", explored)

    candidates = []
    for (difference, centers, _forced_count), (cost, path) in dp.items():
        if centers == 3 and abs(difference) <= tolerance:
            prefix=6 if preferred_actions is not None else 5
            score = ((*cost[:prefix], abs(difference), *cost[prefix:]) if current_snapshot
                     else (abs(difference), *cost))
            candidates.append((score, path))
    if not candidates:
        return SearchResult(None, None, f"在当前模式允许的扇形与三圈站位内，未找到人数差不超过{tolerance}的方案。", explored)
    for _score, path in sorted(candidates, key=lambda item: item[0]):
        eligible = [(index, player.player_key)
                    for index, (player, option) in enumerate(zip(players, path)) if option.hits == 1]
        forced = {player.player_key for player, option in zip(players, path) if option.forced_center}
        selected = sorted(item for item in eligible if item[1] in forced)
        selected.extend(sorted(item for item in eligible if item[1] not in forced)[:3 - len(selected)])
        centers = fixed_centers if fixed_centers is not None else tuple(item[1] for item in sorted(selected))
        center_orders = {key: order for order, key in enumerate(centers, 1)}
        actions = tuple(
            PlayerAction(player.player_key, option.fan,
                         ((1,3) if option.hits==2 and preferred_actions is not None else tuple(range(1, option.hits + 1))) if option.hits >= 2 else
                         (center_orders.get(player.player_key, 1),) if option.hits else ())
            for player, option in zip(players, path) if option.fan or option.hits
        )
        plan = SolverPlan(mode, snapshot_stage, actions, centers,
                          reference_without_tank=tank_key is None and not current_snapshot,
                          current_snapshot=current_snapshot,command_style=preferred_actions is not None)  # type: ignore[arg-type]
        simulation = simulate_plan(players, plan, tolerance=tolerance)
        if simulation.valid:
            return SearchResult(plan, simulation, explored=explored)
    return SearchResult(None, None, "候选方案未通过完整技能顺序校验。", explored)
