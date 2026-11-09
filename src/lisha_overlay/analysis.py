"""Recognition validation, mode-specific correction search, and annotations.

The three modes run distinct legal searches. Hiding some marks from a plan made
for another mode would change its outcome and is never used for real plans.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from typing import Iterable, Literal

from .team_layout import Annotation
from .solver import PlayerAction, SolverPlan, Stage, simulate_plan, solve

AnalysisMode = Literal["all", "fan", "circle"]
BuffColor = Literal["red", "black", "none", "unknown"]
MODES = ("all", "fan", "circle")


@dataclass(frozen=True, slots=True)
class PlayerState:
    player_key: str
    color: BuffColor = "unknown"
    stacks: int | None = None
    isolated: bool = False
    alive: bool = True
    is_tank: bool = False


@dataclass(slots=True)
class AnalysisResult:
    summary: str
    annotations: list[Annotation] = field(default_factory=list)
    status: str = "unavailable"
    details: str = ""
    plan: SolverPlan | None = None
    final_red: int | None = None
    final_black: int | None = None
    difference: int | None = None
    warnings: list[str] = field(default_factory=list)
    current_red: int | None = None
    current_black: int | None = None
    instruction: str = ""
    balanced: bool = False
    reference: bool = False
    strategy: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PlanAnnotation(Annotation):
    role: Literal["center", "helper"] = "helper"


def _mode(mode: str) -> AnalysisMode:
    if mode not in MODES:
        raise ValueError(f"Unknown analysis mode: {mode!r}")
    return mode  # type: ignore[return-value]


def evaluate(players: Iterable[PlayerState], mode: AnalysisMode = "all", *,
             snapshot_stage: Stage = "before_tank", current_snapshot: bool = False,
             current_strategy=None) -> AnalysisResult:
    """Validate a complete snapshot, search the selected mode, and replay it.

    The default snapshot is immediately after 天坠 and before T's mandatory +3
    red. Pass ``after_tank`` if that cast has already occurred. Circle centers
    are assumed selectable by arranging the farthest three targets. Helpers
    may take one circle, consecutive circles 1 and 2, or all three circles.
    HP and movement execution remain external constraints, described in details.
    Without a live designated tank, return a labeled current-buff reference
    that omits the unknown tank event and cannot enforce the tank's fan ban.
    """
    _mode(mode)
    if snapshot_stage not in ("before_tank", "after_tank"):
        raise ValueError(f"Unknown snapshot stage: {snapshot_stage!r}")
    snapshot = list(players)
    if len(snapshot) != 25 or any(not isinstance(player, PlayerState) for player in snapshot):
        return AnalysisResult("待校准", status="needs_calibration", details="需要完整的 25 个团队格子。")
    keys = [player.player_key for player in snapshot]
    valid_keys = {f"g{group}p{slot}" for group in range(1, 9) for slot in range(1, 6)}
    if any(not isinstance(key, str) or key not in valid_keys for key in keys) or len(set(keys)) != 25:
        return AnalysisResult("待校准", status="needs_calibration", details="团队格子编号缺失或重复。")

    for player in snapshot:
        if any(not isinstance(value, bool) for value in (player.alive, player.is_tank)):
            return AnalysisResult("待识别", status="needs_recognition", details="玩家存活、隔绝或坦克状态未确认。")
        if player.color not in ("red", "black", "none", "unknown"):
            return AnalysisResult("待识别", status="needs_recognition", details="Buff 类型未确认。")
        # Dead players do not contribute to the final live-player count. Their
        # invisible buff stack need not prevent reading the surviving roster.
        if not player.alive:
            continue
        if not isinstance(player.isolated,bool):
            return AnalysisResult("待识别", status="needs_recognition", details="玩家存活、隔绝或坦克状态未确认。")
        if player.color == "unknown":
            return AnalysisResult("待识别", status="needs_recognition", details="仍有存活玩家的 Buff 未确认。")
        if player.color in ("red", "black"):
            if isinstance(player.stacks, bool) or not isinstance(player.stacks, int) or not 1 <= player.stacks <= 5:
                return AnalysisResult("待识别", status="needs_recognition", details="仍有存活玩家的 Buff 层数未确认。")
        elif player.stacks is not None and (isinstance(player.stacks, bool) or player.stacks != 0):
            return AnalysisResult("待识别", status="needs_recognition", details="无 Buff 格子的层数不一致。")

    tanks = [] if current_snapshot else [player for player in snapshot if player.is_tank]
    if len(tanks) > 1:
        return AnalysisResult("待指定T", status="needs_calibration", details="不能同时指定多名承伤坦克。")
    reference_without_tank = not current_snapshot and (not tanks or not tanks[0].alive)
    reference_prefix = ""
    reference_details = ""
    if reference_without_tank:
        reference_prefix = "T重伤参考 · " if tanks else "未选T参考 · "
        reference_details = (
            ("指定 T 已重伤，按当前 Buff 参考；" if tanks else "T 未选，按当前 Buff 参考；")
            + ("未计一式 +3 赤煞。" if snapshot_stage == "before_tank"
               else "一式已结算，不重复计入 +3 赤煞。")
            + "承伤 T 不可按扇标执行，指定存活 T 后可重算。\n"
        )

    red_one = sum(player.alive and player.color == "red" and player.stacks == 1 for player in snapshot)
    black_one = sum(player.alive and player.color == "black" and player.stacks == 1 for player in snapshot)
    red_count = sum(p.alive and p.color == 'red' for p in snapshot)
    black_count = sum(p.alive and p.color == 'black' for p in snapshot)
    difference = red_count-black_count
    counts = f"红 {red_count}  黑 {black_count}  差 {difference:+d}" if difference else f"红 {red_count}  黑 {black_count}  差 0"
    if current_snapshot and current_strategy is not None:
        result = current_strategy(snapshot, mode, counts, red_count, black_count)
        if result is not None:
            return result
    if (current_snapshot and abs(difference)<=2 and
            not any(p.alive and p.color in ('red','black') and p.stacks==5 for p in snapshot)):
        return balanced_result(snapshot, mode, counts, red_count, black_count)
    if current_snapshot:
        from .command_solver import solve_commands
        found=solve_commands(snapshot,mode)
    else:
        found = solve(snapshot, mode, snapshot_stage=snapshot_stage,
                      allow_missing_tank=reference_without_tank, current_snapshot=current_snapshot)
    if found.plan is None or found.simulation is None:
        return AnalysisResult(
            counts if current_snapshot else reference_prefix + "当前站位无解", status="no_solution",
            details=reference_details + found.reason + "\n搜索范围：三名不同中心各吃一次；贴圈者连续吃至多三圈；"
            "未搜索中心兼吃其他圈或复杂移动，不把当前搜索失败当成全机制绝对无解。",
            current_red=red_count, current_black=black_count, instruction="无解，等死吧",
        )
    plan, simulation = found.plan, found.simulation
    centers = set(plan.centers)
    annotations: list[Annotation] = []
    for action in plan.actions:
        if action.fan:
            annotations.append(PlanAnnotation(action.player_key, "fan"))
        for order in action.circles:
            annotations.append(PlanAnnotation(action.player_key, "circle", order,
                                              "center" if action.player_key in centers else "helper"))
    center_text = "；".join(f"{order}号中心 {key}" for order, key in enumerate(plan.centers, 1))
    details = (
        reference_details
        + f"{'当前 Buff 参考结算' if reference_without_tank else '预计结算'}：赤 {simulation.red_count}、幽 {simulation.black_count}，差 {simulation.difference}（弱）。\n"
        + center_text + "。\n"
        + ("按当前已识别 Buff 计算，不预加一式层数。" if current_snapshot else "" if reference_without_tank else
           "一式尚未结算，已计入指定 T 的三层赤煞。" if snapshot_stage == "before_tank"
           else "一式已结算，本次不重复计入 T 的三层赤煞。")
        + ("\n站位前提：三名中心为可控最远点名，各只吃自身那一圈。" if plan.command_style else
           "\n站位前提：三名中心为可控最远点名，各只吃自身那一圈；标记 1、2 的双圈者站一二号交集。")
        + "\n未模拟当前气血和移动速度；需能承受技能本身伤害。"
    )
    if plan.command_style:
        details+='\n引导约定：1号定点，2号单独远离，3号在1号旁预留另一点；三名中心彼此不吃到其他圈。'
        details+='\n除三名引导和隔绝者外，按本帧Buff分组；其余避圈。1→3表示一号结算后再集合三号，二号不进大团。'
    if any(len(action.circles) == 3 for action in plan.actions):
        details += "\n含三圈重叠：三中心按三角分站并避开其他中心的圈，标记1、2、3的贴圈者站三圈公共交集。"
    elif not plan.command_style:
        details += "\n第三圈与一二号交集分开；不需要中心兼吃其他圈或复杂移动。"
    if simulation.strong_cancellations:
        details += f"\n含 {simulation.strong_cancellations} 次强消融（约45%最大气血），须提前保证气血；后续隔绝已计入。"
    instruction=plan_instruction(snapshot,plan)
    complex_fallback=current_snapshot and not plan.command_style
    if complex_fallback:
        instruction='复杂解：'+instruction
        details='未找到简洁整组站位；以下为需精细分配的候选，不能直接当作一句话口令。\n'+details
    return AnalysisResult(
        counts if current_snapshot else reference_prefix + f"红1 {red_one} · 黑1 {black_one}", annotations, "solved", details,
        plan, simulation.red_count, simulation.black_count, simulation.difference,
        current_red=red_count, current_black=black_count, instruction=instruction,
        reference=complex_fallback,
    )


def evaluate_current(players: Iterable[PlayerState], mode: AnalysisMode = 'all', *,
                     snapshot_stage: Stage = 'after_tank') -> AnalysisResult:
    """Live overlay policy: count the observed roster; no T selection/event."""
    return evaluate(players, mode, snapshot_stage='after_tank', current_snapshot=True)


def balanced_result(players, mode, counts, red, black):
    """No correction marks when already weak; mandatory circles stay neutral."""
    guides=sorted((p for p in players if p.alive and p.color=='black' and p.stacks in (1,2)),
                  key=lambda p:tuple(map(int,p.player_key[1:].split('p'))))[:3]
    plan=None
    instruction='黑一/黑二引导黑圈'
    details='当前人数差已在2人以内；不安排吃扇形、消Buff或个人标记。'
    warnings=[]
    if len(guides)==3:
        centers=tuple(p.player_key for p in guides)
        plan=SolverPlan(mode,'after_tank',tuple(PlayerAction(key,False,(order,))
            for order,key in enumerate(centers,1)),centers,current_snapshot=True)
        checked=simulate_plan(players,plan)
        if not checked.valid:
            raise ValueError('中性黑圈引导方案未通过校验：'+checked.reason)
        details+='\n黑圈仍须处理：黑一/黑二各引导一圈，人员互不重叠，红黑人数保持不变。'
        details+='\n可用引导格：'+'、'.join(centers)+'。'
    else:
        instruction='黑一/黑二不足3人，圈需另排'
        warnings=['当前人数已平衡，但黑一/黑二不足三名，黑圈需另行安排。']
        details+='\n'+warnings[0]
    return AnalysisResult(counts+'  平衡',status='solved',details=details,plan=plan,
        final_red=red,final_black=black,difference=abs(red-black),warnings=warnings,
        current_red=red,current_black=black,instruction=instruction,balanced=True)


def plan_instruction(players, plan):
    """Describe the actual assigned marks, including helpers and repeated hits."""
    if plan.command_style:
        from .command_solver import command_instruction
        return command_instruction(players,plan)
    by_key={p.player_key:p for p in players}
    centers=set(plan.centers)
    groups={}
    for action in plan.actions:
        p=by_key[action.player_key]
        label=('隔绝' if p.isolated else '无Buff' if p.color=='none' else
               ('红' if p.color=='red' else '黑')+'一二三四'[p.stacks-1])
        if action.fan:groups.setdefault('吃扇形',Counter())[label]+=1
        if action.circles:
            verb=('再' if action.fan else '')+('排圈' if action.player_key in centers else
                 '贴'+('/'.join(map(str,action.circles)))+'圈')
            groups.setdefault(verb,Counter())[label]+=1
    verbs=sorted(groups, key=lambda verb:(verb!='吃扇形', '排圈' not in verb, verb.startswith('再'), verb))
    return '；'.join('、'.join(f'{n}{label}' for label,n in groups[verb].items())+verb
                    for verb in verbs) or '保持站位'


def demo_result(mode: AnalysisMode = "all") -> AnalysisResult:
    """Return visual fixtures only; circle digits are strike order, not stacks.

    Test fixtures intentionally demonstrate both fan marks and each of the
    three numbered circles. They are not claimed to solve a combat snapshot.
    A real solver must search each selected mode independently.
    """
    _mode(mode)
    annotations = [
        Annotation("g1p2", "fan", 1),
        Annotation("g4p3", "fan", 1),
        Annotation("g1p4", "circle", 1),
        Annotation("g2p3", "circle", 2),
        Annotation("g5p2", "circle", 3),
    ]
    if mode != "all":
        annotations = [item for item in annotations if item.kind == mode]
    return AnalysisResult(
        "测试 · 红 6 黑 8 差 -2",
        annotations=annotations,
        status="demo",
        details="测试区域与模拟标记；圈内 1、2、3 表示圈序，不是 Buff 层数或当前实战方案。",
        current_red=6, current_black=8,
        instruction='测试 · '+('2黑一吃扇形；3红一排圈' if mode=='all' else
                              '2黑一吃扇形' if mode=='fan' else '3红一排圈'),
    )
