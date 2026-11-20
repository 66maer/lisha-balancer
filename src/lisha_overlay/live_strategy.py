"""Use the reviewed laboratory policy in the packaged capture pipeline."""

from functools import lru_cache
from dataclasses import replace
from pathlib import Path

from .analysis import AnalysisResult, PlanAnnotation, evaluate
from .difficulty import SAFE_TOLERANCE
from .solver import BuffState, PlayerAction, SolverPlan, receive_buff, simulate_plan
from .spoken_catalogue import SpokenCatalogue
from .step_strategy import command_lines, compact_row, solve_steps
from .strategy_catalogue import Catalogue
from .spoken_planner import CallPlanner, buff_instruction
from .xinfa import guide_cost


@lru_cache(maxsize=1)
def prepare_live_strategy():
    base = Path(__file__).parent / "assets" / "strategy"
    return SpokenCatalogue(Catalogue(base), base)


def _reviewed_result(players, mode, counts, red, black, *, prefer_exact=False, tolerance=2):
    expected = {f"g{team}p{slot}" for team in range(1, 6) for slot in range(1, 6)}
    if (
        mode not in ("all", "circle")
        or {p.player_key for p in players} != expected
        or any(not p.alive or p.isolated or (p.stacks or 0) > 3 for p in players)
    ):
        return None
    roster = [
        dict(key=p.player_key, value=0 if p.color == "none" else p.stacks * (-1 if p.color == "black" else 1))
        for p in players
    ]
    data = solve_steps(roster, mode, prepare_live_strategy(), tolerance=tolerance, prefer_exact=prefer_exact)
    if data['status'] == 'complex':
        return None
    strategy = dict(
        policy="reviewed_few_participants",
        preference="exact" if prefer_exact else "minimum",
        status=data["status"],
        solver_ms=data["elapsed_ms"],
    )
    if data["status"] != "solved":
        return AnalysisResult(
            counts,
            status="no_solution",
            instruction=data["prompt"],
            current_red=red,
            current_black=black,
            strategy=strategy,
            details="当前七状态模型内未找到合法配平。",
        )
    board = data
    if prefer_exact and data["quota_variants"]:
        board = min(
            data["quota_variants"],
            key=lambda item: (abs(item["board"]["final"]["difference"]), item["count"]),
        )["board"]
    ordered = data["roster"]
    centers = tuple(ordered[i]["key"] for i in board["guides"])
    actions = []
    for index, code in enumerate(board["actions"]):
        if not code:
            continue
        key = ordered[index]["key"]
        circles = (
            (centers.index(key) + 1,)
            if key in centers
            else {0: (), 1: (1,), 2: (1, 3), 3: (1, 2, 3)}[code % 4]
        )
        actions.append(PlayerAction(key, bool(code // 4), circles))
    plan = SolverPlan(
        mode,
        "after_tank",
        tuple(actions),
        centers,
        current_snapshot=True,
        command_style=True,
        spoken_policy=True,
    )
    checked = simulate_plan(players, plan, tolerance=tolerance)
    if (
        not checked.valid
        or checked.red_count != board["final"]["red"]
        or checked.black_count != board["final"]["black"]
    ):
        raise ValueError("提词方案独立验算失败：" + checked.reason)
    annotations = []
    for action in actions:
        if action.fan:
            annotations.append(PlanAnnotation(action.player_key, "fan"))
        for circle in action.circles:
            annotations.append(
                PlanAnnotation(
                    action.player_key,
                    "circle",
                    circle,
                    "center" if action.player_key in centers else "helper",
                )
            )
    neutral_balance = abs(red - black) <= tolerance and all(
        not action.fan and action.player_key in centers for action in actions
    )
    if neutral_balance:
        annotations = []
    cues = [
        dict(id=cue["id"], title=cue["title"], call=cue["call"], rows=cue["rows"]) for cue in board["cues"]
    ]
    strategy.update(
        cues=cues,
        guide_keys=list(centers),
        final=board["final"],
        extra_named_count=board["extra_named_count"],
    )
    if board["quota"]:
        row = next(row for cue in cues for row in cue["rows"] if row.get("quota"))
        strategy["quota"] = dict(
            selected_count=row["count"],
            minimum=row["minimum_count"],
            maximum=row["maximum_count"],
            balanced_count=row["balanced_count"],
        )
    details = (
        f"预计红 {checked.red_count} 黑 {checked.black_count}，差 {checked.red_count - checked.black_count:+d}。\n"
        "引导1去主点，引导2单走，引导3去旁点。双圈站主点与旁点交集，三圈另用三角交集。\n"
        "按当前Buff计算。按队伍位置优先安排1、2队，其次3、4队，5队最后候补。"
    )
    if "quota" in strategy:
        quota = strategy["quota"]
        details += f"\n当前标记{quota['selected_count']}人，有效人数{quota['minimum']}～{quota['maximum']}。"
        if quota["balanced_count"]:
            details += f" {quota['balanced_count']}人可正平。"
    if checked.strong_cancellations:
        details += f"\n强消融{checked.strong_cancellations}次，需保证血量。"
    return AnalysisResult(
        counts,
        annotations,
        "solved",
        details,
        plan,
        checked.red_count,
        checked.black_count,
        checked.difference,
        current_red=red,
        current_black=black,
        instruction='\n'.join(command_lines([row for cue in cues for row in cue['rows']])),
        buff_instruction=buff_instruction(players, plan) if annotations else '',
        balanced=neutral_balance,
        strategy=strategy,
    )


def _evaluate(players, mode, *, snapshot_stage, prefer_exact=False, difficulty='normal', spoken=False):
    players = list(players)
    tolerance = SAFE_TOLERANCE[difficulty]
    tanks = [p for p in players if p.is_tank]
    tank = tanks[0] if tanks else None
    tank_phase = mode != 'circle' and tank is not None
    if tank_phase and len(tanks) == 1:
        group, slot = tank.player_key[1:].split('p')
        label = f'{group}队{slot} 主T'
        if tank.alive is False:
            return AnalysisResult('主T重伤', status='tank_warning', instruction='换T',
                                  details=f'{label}已重伤，请换T并重新点选。')
        if snapshot_stage == 'before_tank' and tank.color == 'red' and tank.stacks in (2, 3, 4, 5):
            return AnalysisResult(f'{label} · 赤{tank.stacks}', status='tank_warning',
                                  instruction='换T，要炸',
                                  details=f'{label}已有{tank.stacks}层赤，一式再叠3层将达到自爆阈值。')

    if spoken:
        return _spoken_result(players, mode, snapshot_stage, tank_phase, prefer_exact, tolerance)

    def provider(snapshot, selected_mode, counts, red, black):
        return _reviewed_result(snapshot, selected_mode, counts, red, black,
                                prefer_exact=prefer_exact, tolerance=tolerance)

    result = evaluate(
        players, mode, snapshot_stage=snapshot_stage, current_snapshot=not tank_phase,
        current_strategy=provider, tolerance=tolerance, avoid_overlap=True, prefer_exact=prefer_exact,
    )
    if prefer_exact and result.strategy and result.difference:
        expanded = evaluate(players, mode, snapshot_stage=snapshot_stage, current_snapshot=True,
                            tolerance=tolerance, avoid_overlap=True, prefer_exact=True)
        if expanded.difference is not None and expanded.difference < result.difference:
            result = expanded
    if mode != 'circle' and tank is None and snapshot_stage == 'before_tank' and result.status in ('solved', 'no_solution'):
        result.reference = True
        result.details = '主T未选，本方案按团框现状计算，点选主T后可预演一式。\n' + result.details
    if result.strategy or result.status not in ("solved", "no_solution"):
        return result
    result.strategy = dict(policy="verified_tank_simulation" if tank_phase else "verified_extended_state_fallback",
                           preference="exact" if prefer_exact else "minimum")
    if tank_phase and result.current_red is not None:
        diff = result.current_red - result.current_black
        result.summary = f'红 {result.current_red}  黑 {result.current_black}  差 {diff:+d}'
    if not tank_phase:
        result.reference = True
        result.details = "使用扩展状态后备验算。\n" + result.details
    if result.balanced and result.plan is None:
        result.instruction = '无需吃扇\n引导圈需另排\n无需蹭圈'
    if result.plan is not None:
        actions = tuple(
            replace(action, circles=(1, 3))
            if action.player_key not in result.plan.centers and len(action.circles) == 2
            else action
            for action in result.plan.actions
        )
        result.plan = replace(result.plan, actions=actions, command_style=True, spoken_policy=True)
        checked = simulate_plan(players, result.plan, tolerance=tolerance)
        if not checked.valid:
            raise ValueError("扩展状态站位复核失败：" + checked.reason)
        result.final_red, result.final_black, result.difference = checked.red_count, checked.black_count, checked.difference
        if not result.balanced:
            result.annotations = []
            for action in actions:
                if action.fan:
                    result.annotations.append(PlanAnnotation(action.player_key, "fan"))
                result.annotations.extend(
                    PlanAnnotation(
                        action.player_key,
                        "circle",
                        circle,
                        "center" if action.player_key in result.plan.centers else "helper",
                    )
                    for circle in action.circles
                )
        by_key = {p.player_key: i for i, p in enumerate(players)}
        roster = [
            dict(
                name=p.player_key,
                team=int(p.player_key[1]),
                value=(p.stacks or 0) * (-1 if p.color == "black" else 1),
            )
            for p in players
        ]
        states = [BuffState(p["value"], source.isolated, source.alive) for p, source in zip(roster, players)]
        if tank_phase and snapshot_stage == 'before_tank':
            index = by_key[tank.player_key]
            states[index] = receive_buff(states[index], 'red', 3).state
        after_fan = list(states)
        for action in actions:
            if action.fan:
                index = by_key[action.player_key]
                after_fan[index] = receive_buff(after_fan[index], "red").state
        groups = [
            ("fan", "吃扇", [by_key[a.player_key] for a in result.plan.actions if a.fan]),
            ("guide", "引导", [by_key[key] for key in result.plan.centers]),
        ]
        groups += [
            (
                "circle",
                ("蹭圈", "蹭双圈", "蹭三圈")[hits - 1],
                [
                    by_key[a.player_key]
                    for a in result.plan.actions
                    if a.player_key not in result.plan.centers and len(a.circles) == hits
                ],
            )
            for hits in (1, 2, 3)
        ]
        rows = [
            compact_row(roster, states if role == "fan" else after_fan, selected, action, role)
            for role, action, selected in groups
            if selected
        ]
        result.instruction = '\n'.join(command_lines(rows))
        if result.annotations:
            result.buff_instruction = buff_instruction(players, result.plan)
        result.strategy["rows"] = rows
        result.details += "\n统一站位：1主点、2单走、3旁点。双圈用1与3交集，三圈用三角交集。"
    return result


def evaluate_current(players, mode="all", *, snapshot_stage="after_tank", difficulty='normal'):
    return _evaluate(players, mode, snapshot_stage=snapshot_stage, difficulty=difficulty)


def evaluate_exact(players, mode="all", *, snapshot_stage="after_tank", difficulty='normal'):
    return _evaluate(players, mode, snapshot_stage=snapshot_stage, prefer_exact=True, difficulty=difficulty)


def _spoken_result(players, mode, stage, tank_phase, exact, tolerance):
    planner = CallPlanner(players)

    def rank(plan, simulation):
        effort = planner.compile(plan)[2]
        score = (simulation.strong_cancellations, *effort)
        return (simulation.difference, *score) if exact else (*score, simulation.difference)

    result = evaluate(players, mode, snapshot_stage=stage, current_snapshot=not tank_phase,
                      tolerance=tolerance, avoid_overlap=True, prefer_exact=exact, plan_rank=rank,
                      guide_penalties={player.player_key: guide_cost(player) for player in players})
    if not tank_phase and mode != 'circle' and stage == 'before_tank' and result.status in ('solved', 'no_solution'):
        result.reference = True
        result.details = '主T未选，按团框现状计算。\n' + result.details
    if result.plan is None:
        return result
    if any(player.xinfa for player in players):
        original = result.plan
        alternate = planner.refine(original, lambda plan: planner.compile(plan)[2])
        if alternate != original:
            checked = simulate_plan(players, alternate, tolerance=tolerance)
            if checked.valid and (checked.red_count, checked.black_count) == (result.final_red, result.final_black):
                result.plan = alternate
                result.annotations = [annotation for action in alternate.actions for annotation in (
                    ([PlanAnnotation(action.player_key, 'fan')] if action.fan else []) + [
                        PlanAnnotation(action.player_key, 'circle', order,
                                       'center' if action.player_key in alternate.centers else 'helper')
                        for order in action.circles
                    ]
                )]
                original_centers = '；'.join(f'{order}号中心 {key}' for order, key in enumerate(original.centers, 1))
                new_centers = '；'.join(f'{order}号中心 {key}' for order, key in enumerate(alternate.centers, 1))
                result.details = result.details.replace(original_centers, new_centers)
    result.instruction, groups, effort = planner.compile(result.plan)
    result.buff_instruction = buff_instruction(players, result.plan)
    result.balanced = (
        result.plan.current_snapshot and not any(player.xinfa for player in players)
        and all(not action.fan and action.player_key in result.plan.centers for action in result.plan.actions)
        and result.difference == abs(result.current_red - result.current_black)
    )
    result.strategy = dict(policy='spoken_groups', preference='exact' if exact else 'simple',
                           groups=groups, group_count=len(groups), effort=effort[0],
                           guide_keys=list(result.plan.centers))
    return result


def evaluate_simple_commands(players, mode='all', *, snapshot_stage='after_tank', difficulty='normal'):
    return _evaluate(players, mode, snapshot_stage=snapshot_stage, difficulty=difficulty, spoken=True)


def evaluate_exact_commands(players, mode='all', *, snapshot_stage='after_tank', difficulty='normal'):
    return _evaluate(players, mode, snapshot_stage=snapshot_stage, prefer_exact=True, difficulty=difficulty, spoken=True)
