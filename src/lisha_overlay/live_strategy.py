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
    data = solve_steps(roster, mode, prepare_live_strategy(), tolerance=tolerance)
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
        balanced=neutral_balance,
        strategy=strategy,
    )


def _evaluate(players, mode, *, prefer_exact=False, difficulty='normal'):
    players = list(players)
    tolerance = SAFE_TOLERANCE[difficulty]

    def provider(snapshot, selected_mode, counts, red, black):
        return _reviewed_result(snapshot, selected_mode, counts, red, black,
                                prefer_exact=prefer_exact, tolerance=tolerance)

    result = evaluate(
        players, mode, snapshot_stage="after_tank", current_snapshot=True, current_strategy=provider,
        tolerance=tolerance, avoid_overlap=True,
    )
    if result.strategy or result.status not in ("solved", "no_solution"):
        return result
    result.strategy = dict(policy="verified_extended_state_fallback")
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
        result.strategy["rows"] = rows
        result.details += "\n统一站位：1主点、2单走、3旁点。双圈用1与3交集，三圈用三角交集。"
    return result


def evaluate_current(players, mode="all", *, snapshot_stage="after_tank", difficulty='normal'):
    return _evaluate(players, mode, difficulty=difficulty)


def evaluate_exact(players, mode="all", *, snapshot_stage="after_tank", difficulty='normal'):
    return _evaluate(players, mode, prefer_exact=True, difficulty=difficulty)
