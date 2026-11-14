"""Squad-aware spoken plans, with immutable cohorts and event-by-event replay.

The live overlay and local strategy laboratory share this planner. Its search
uses whole five-person columns as baselines, then at most five named exceptions
and three separately budgeted guides. The historical histogram catalogue is a
verified fallback. Squad arrangements have been checked through sampled cases.
"""

from functools import lru_cache
from collections import Counter
from time import perf_counter

import numpy as np

from lisha_overlay.solver import BuffState, IllegalAction, receive_buff

INF = np.int64(10**17)
RISK = 10**14
TRIPLE_CIRCLE = 10**12
REPEATED_CIRCLE = 10**10
FIFTH_TEAM_ACTION = 10**8
HELPER_ACTION = 10**6
MELEE_ACTION = 10**4
TEAM_CHANGE = 1
NUMERALS = "一二三四五"
LABELS = ("黑三", "黑二", "黑一", "无Buff", "红一", "红二", "红三")


def validate_roster(roster):
    if not isinstance(roster, list) or len(roster) != 25:
        raise ValueError("需要五队，每队五人。")
    expected = {f"g{g}p{s}" for g in range(1, 6) for s in range(1, 6)}
    if any(not isinstance(p, dict) for p in roster):
        raise ValueError("人员格式错误。")
    if {p.get("key") for p in roster} != expected:
        raise ValueError("队伍格子缺失或重复。")
    result = []
    for p in sorted(roster, key=lambda p: p["key"]):
        value = p.get("value")
        if type(value) is not int or not -3 <= value <= 3:
            raise ValueError("当前分步实验支持黑三至红三；四层、死亡、已有隔绝尚未纳入。")
        if p.get("isolated", False) or p.get("alive", True) is not True:
            raise ValueError("当前分步实验尚未纳入死亡和已有隔绝。")
        name = p.get("name", "")
        if not isinstance(name, str) or len(name) > 24:
            raise ValueError("名字最多24字。")
        result.append(
            dict(
                key=p["key"],
                team=int(p["key"][1]),
                slot=int(p["key"][3]),
                name=name.strip() or f"{NUMERALS[int(p['key'][1]) - 1]}队{p['key'][3]}",
                value=value,
            )
        )
    names = [p["name"] for p in result]
    if len(set(names)) != 25:
        raise ValueError("名字请保持唯一，便于点名。")
    return result


def roster_from_counts(counts, shuffle=False, seed=0):
    if len(counts) != 7 or any(type(n) is not int or n < 0 for n in counts) or sum(counts) != 25:
        raise ValueError("七种人数之和必须是25。")
    values = [i - 3 for i, n in enumerate(counts) for _ in range(n)]
    if shuffle:
        np.random.default_rng(seed).shuffle(values)
    return [dict(key=f"g{i // 5 + 1}p{i % 5 + 1}", value=int(value)) for i, value in enumerate(values)]


@lru_cache(maxsize=1)
def event_tables():
    valid = np.zeros((7, 8), bool)
    delta = np.zeros((7, 8), np.int8)
    cost = np.zeros((7, 8), np.int64)
    for si in range(7):
        for code in range(8):
            try:
                state = BuffState(si - 3)
                risk = weak = 0
                for color in (["red"] if code // 4 else []) + ["black"] * (code % 4):
                    event = receive_buff(state, color)
                    state = event.state
                    risk += event.strong
                    weak += event.weak
                valid[si, code] = True
                delta[si, code] = (state.value > 0) - (state.value < 0)
                cost[si, code] = risk * RISK + (code // 4 + code % 4) * 100 + weak * 10
            except IllegalAction:
                pass
    return valid, delta, cost


def shifted(shape, dg, de, dd):
    """Slices for adding one independent local contribution to DP axes."""
    return (
        (
            slice(0, shape[-3] - dg),
            slice(0, shape[-2] - de),
            slice(max(0, -dd), min(shape[-1], shape[-1] - dd)),
        ),
        (slice(dg, shape[-3]), slice(de, shape[-2]), slice(max(0, dd), min(shape[-1], shape[-1] + dd))),
    )


@lru_cache(maxsize=256)
def squad_options(values, mode, team):
    valid, delta, costs = event_tables()
    scores = np.full((8, 4, 6, 11), INF, np.int64)
    paths = np.zeros(scores.shape, np.uint32)
    for baseline in range(8 if mode == "all" else 4):
        dp = np.full((4, 6, 11), INF, np.int64)
        dp[0, 0, 5] = 0
        path = np.zeros(dp.shape, np.uint32)
        for slot, value in enumerate(values):
            ndp = np.full(dp.shape, INF, np.int64)
            npath = np.zeros(dp.shape, np.uint32)
            options = [(a, 0, int(a != baseline), a) for a in range(8 if mode == "all" else 4)]
            options += [(8 + fan, 1, 0, fan * 4 + 1) for fan in range(2 if mode == "all" else 1)]
            for encoded, guides, extra, action in options:
                if not valid[value + 3, action]:
                    continue
                dd = int(delta[value + 3, action])
                src, dst = shifted(dp.shape, guides, extra, dd)
                participating = bool(guides or action)
                helper = participating and not guides
                position_cost = participating * (team * 5 + slot)
                role_cost = (
                    participating * (team == 4) * FIFTH_TEAM_ACTION
                    + int(action % 4 == 3) * TRIPLE_CIRCLE
                    + int(action % 4 == 2) * REPEATED_CIRCLE
                    + helper * HELPER_ACTION
                    + participating * (team in (2, 3)) * MELEE_ACTION
                )
                candidate = dp[src] + costs[value + 3, action] + role_cost + position_cost
                take = candidate < ndp[dst]
                ndp[dst][take] = candidate[take]
                npath[dst][take] = (path[src] | np.uint32(encoded << (slot * 4)))[take]
            dp, path = ndp, npath
        scores[baseline], paths[baseline] = dp, path
    # Cache contents are never changed by callers.
    return scores, paths


def squad_candidates(roster, mode, tolerance=2):
    """Exact local DP; keep compact team-baseline paths for eight common bodies.

    The local merge keeps the least strong damage / fifth-team load / helpers
    for each (guide count, extra count, holder difference).  It does not assert
    global optimality over arbitrary speech or all Buff selector combinations.
    """
    bases = 8 if mode == "all" else 4
    shape = (bases, 4, 6, 51)
    dp = np.full(shape, INF, np.int64)
    dp[:, 0, 0, 25] = 0
    back = []
    local_paths = []
    for team in range(5):
        values = tuple(p["value"] for p in roster[team * 5 : team * 5 + 5])
        local, paths = squad_options(values, mode, team)
        local_paths.append(paths)
        # At each local inventory, choose which whole-team instruction to use.
        penalties = (np.arange(bases)[:, None] != np.arange(8)[None, :]) * TEAM_CHANGE
        options = local[None, ...] + penalties[:, :, None, None, None]
        which = options.argmin(axis=1)
        best = options.min(axis=1)
        ndp = np.full(shape, INF, np.int64)
        pointer = np.full(shape, -1, np.int16)
        for guides in range(4):
            for extra in range(6 - guides):
                for difference in range(-5, 6):
                    local_cost = best[:, guides, extra, difference + 5]
                    if np.all(local_cost >= INF):
                        continue
                    src, dst = shifted(shape, guides, extra, difference)
                    src, dst = (slice(None),) + src, (slice(None),) + dst
                    candidate = dp[src] + local_cost[:, None, None, None]
                    take = candidate < ndp[dst]
                    ndp[dst][take] = candidate[take]
                    choice = which[:, guides, extra, difference + 5]
                    encoded = ((choice * 4 + guides) * 6 + extra) * 11 + difference + 5
                    pointer[dst][take] = np.broadcast_to(encoded[:, None, None, None], candidate.shape)[take]
        dp = ndp
        back.append(pointer)
    seen = set()
    for body in range(bases):
        for extra_total in range(6):
            for final in range(-tolerance, tolerance + 1):
                if dp[body, 3, extra_total, final + 25] >= INF:
                    continue
                guides, extra, difference = 3, extra_total, final + 25
                actions = [0] * 25
                centers = []
                exceptions = []
                for team in range(4, -1, -1):
                    encoded = int(back[team][body, guides, extra, difference])
                    base, remainder = divmod(encoded, 4 * 6 * 11)
                    dg, remainder = divmod(remainder, 6 * 11)
                    de, di = divmod(remainder, 11)
                    path = int(local_paths[team][base, dg, de, di])
                    for slot in range(5):
                        index = team * 5 + slot
                        action = (path >> (4 * slot)) & 15
                        if action >= 8:
                            centers.append(index)
                            actions[index] = (action - 8) * 4 + 1
                        else:
                            actions[index] = action
                            if action != base:
                                exceptions.append(index)
                    guides -= dg
                    extra -= de
                    difference -= di - 5
                assert (guides, extra, difference) == (0, 0, 25)
                key = (tuple(actions), tuple(sorted(centers)))
                if key in seen:
                    continue
                seen.add(key)
                yield dict(actions=actions, guides=sorted(centers), extras=sorted(exceptions), source="squad")


def catalogue_candidate(roster, mode, catalogue):
    """Assign real column/slot identities to one historical histogram plan."""
    counts = [sum(p["value"] == value for p in roster) for value in range(-3, 4)]
    data = catalogue.solve(counts, mode)
    if data["source"] == "none":
        return None
    pools = [[i for i, p in enumerate(roster) if p["value"] == value] for value in range(-3, 4)]
    actions = [0] * 25
    guides = []
    for role in data["guide_roles"]:
        index = pools[role["state"] + 3].pop(0)
        guides.append(index)
        actions[index] = int(role["fan"]) * 4 + 1
    extras = []
    if data["source"] == "bulk":
        for pool in pools:
            for index in pool:
                actions[index] = data["baseline"]
        for item in data["exceptions"]:
            index = pools[item["state"] + 3].pop(0)
            extras.append(index)
            actions[index] = int(item["fan"]) * 4 + item["hits"]
    else:
        verbs = {"不动": 0, "吃扇": 4, "去主点": 1, "主点出圈后去旁点": 2, "三圈重叠": 3, "—": 0}
        for row in data["rows"]:
            pool = pools[LABELS.index(row["buff"])]
            for index in pool:
                actions[index] = verbs[row["normal"]]
            for item in row["exceptions"]:
                index = pool.pop(0)
                extras.append(index)
                actions[index] = verbs[item["action"]]
    return dict(actions=actions, guides=guides, extras=extras, source="histogram")


def mask_of(indices):
    return sum(1 << i for i in indices)


def members(mask):
    return [i for i in range(25) if mask & (1 << i)]


def selector_options(roster, states, universe, all_label):
    """Closed finite selectors; every spoken target has a machine-readable set."""
    yield dict(mask=universe, label=all_label, kind="all", cost=1.0)
    teams = [mask_of(i for i, p in enumerate(roster) if p["team"] == team) & universe for team in range(1, 6)]
    for bits in range(1, 31):
        mask = 0
        labels = []
        for team in range(5):
            if bits & (1 << team):
                mask |= teams[team]
                if teams[team]:
                    labels.append(NUMERALS[team])
        if mask:
            yield dict(mask=mask, label="、".join(labels) + "队", kind="team", cost=1.25 + 0.08 * len(labels))
    atomic = [mask_of(i for i in members(universe) if states[i].value == value) for value in range(-3, 4)]
    basic = [(atomic[si], label) for si, label in enumerate(LABELS) if atomic[si]]
    for value in (1, 2, 3):
        mask = atomic[3 - value] | atomic[3 + value]
        if mask:
            basic.append((mask, "身上" + "一二三"[value - 1] + "层的"))
    basic += [
        (mask_of(i for i in members(universe) if states[i].value < 0), "黑Buff的"),
        (mask_of(i for i in members(universe) if states[i].value > 0), "红Buff的"),
        (universe & ~atomic[3], "有Buff的"),
    ]
    for mask, label in basic:
        if mask:
            yield dict(mask=mask, label=label, kind="buff", cost=2.0)
    # Arbitrary unions of exact Buff states retain the old catalogue's language.
    for bits in range(1, 128):
        used = [si for si in range(7) if bits & (1 << si) and atomic[si]]
        if len(used) < 2:
            continue
        mask = 0
        for si in used:
            mask |= atomic[si]
        yield dict(
            mask=mask,
            label="、".join(LABELS[si] for si in used),
            kind="buff",
            cost=2 + 0.65 * (len(used) - 1),
        )


def compile_selector(roster, states, universe, target, allowed, all_label):
    if not target:
        return dict(
            label="",
            kind="none",
            selected=[],
            named=[],
            cost=0,
            target_mask=0,
            mask=0,
            include=[],
            exclude=[],
        )
    candidates = []
    for option in selector_options(roster, states, universe, all_label):
        difference = option["mask"] ^ target
        if difference & ~allowed:
            continue
        include = target & ~option["mask"]
        exclude = option["mask"] & ~target
        cost = option["cost"] + difference.bit_count() * 1.5
        candidates.append((cost, difference.bit_count(), option, include, exclude))
    if target & ~allowed == 0:
        option = dict(mask=0, label="", kind="named", cost=0)
        candidates.append((1 + target.bit_count() * 1.5, target.bit_count(), option, target, 0))
    if not candidates:
        return None
    cost, _, option, include, exclude = min(candidates, key=lambda item: (item[0], item[1], item[2]["label"]))
    label = option["label"]
    if exclude:
        label += "（" + "、".join(roster[i]["name"] for i in members(exclude)) + "除外）"
    if include:
        label += ("，加" if label else "") + "、".join(roster[i]["name"] for i in members(include))
    return dict(
        label=label,
        kind=option["kind"],
        selected=members(target),
        named=members(include | exclude),
        cost=cost,
        target_mask=target,
        mask=option["mask"],
        include=members(include),
        exclude=members(exclude),
    )


def state_payload(roster, states):
    return [dict(p, value=state.value, isolated=state.isolated) for p, state in zip(roster, states)]


def totals(states):
    red = sum(state.value > 0 for state in states)
    black = sum(state.value < 0 for state in states)
    return dict(red=red, black=black, difference=red - black, balanced=abs(red - black) <= 2)


def count_words(count):
    digits = "零一二三四五六七八九"
    if count < 10:
        return digits[count]
    tens, units = divmod(count, 10)
    return (digits[tens] if tens > 1 else "") + "十" + (digits[units] if units else "")


def compact_row(roster, states, selected, action, role, selector=None, reference="after_fan"):
    """Preserve the exact recipients while rendering count / Buff / action."""
    counts = Counter(states[i].value for i in selected)
    buffs = [
        dict(
            value=value,
            count=count,
            label=("红" if value > 0 else "黑") + str(abs(value)) if value else "无Buff",
        )
        for value, count in sorted(counts.items(), reverse=True)
    ]
    teams = []
    include_names = []
    exclude_names = []
    scope = ""
    if selector:
        if selector["kind"] == "team":
            teams = sorted({roster[i]["team"] for i in members(selector["mask"])})
        elif selector["kind"] == "all":
            scope = "全团" if role == "fan" else "大团"
        include_names = [roster[i]["name"] for i in selector["include"]]
        exclude_names = [roster[i]["name"] for i in selector["exclude"]]
    row = dict(
        role=role,
        count=len(selected),
        count_text=scope or count_words(len(selected)) + "人",
        buffs=buffs,
        action=action,
        selected=list(selected),
        teams=teams,
        include_names=include_names,
        exclude_names=exclude_names,
        reference=reference,
    )
    if selector and selector["kind"] == "quota":
        row["quota"] = True
    row["text"] = compact_text(row)
    return row


def compact_text(row):
    buff_tags = []
    if row["count_text"] not in ("全团", "大团"):
        for buff in row["buffs"]:
            label = buff["label"]
            if len(row["buffs"]) > 1:
                label += "×" + str(buff["count"])
            buff_tags.append("[" + label + "]")
    text = " ".join([row["count_text"], *buff_tags, row["action"]])
    if row.get("team_choices"):
        text += " (" + "/".join(f"[{team}队]" for team in row["team_choices"]) + ")"
    elif row["teams"]:
        text += " (" + "+".join(f"[{team}队]" for team in row["teams"]) + ")"
    names = []
    if row["include_names"]:
        names.append("加" + "、".join(row["include_names"]))
    if row["exclude_names"]:
        names.append("除" + "、".join(row["exclude_names"]))
    if names:
        text += " (" + " / ".join(names) + ")"
    if row["reference"] == "initial" and row["role"] == "circle":
        text += " [扇前Buff]"
    return text


def command_lines(rows):
    """Render the assigned recipients in the three fixed command rows."""
    groups = {'fan': [], 'guide': [], 'circle': []}
    actions = {'吃扇': '吃扇', '引导': '引导圈', '蹭圈': '蹭单圈',
               '蹭双圈': '蹭双圈', '蹭三圈': '蹭三圈'}
    for row in rows:
        buffs = '、'.join(f"{{{buff['label']}}} × {buff['count']}" for buff in row['buffs'])
        groups[row['role']].append(f"{buffs} {actions[row['action']]}")
    return [
        '，'.join(groups['fan']) or '无需吃扇',
        '，'.join(groups['guide']) or '引导圈需另排',
        '，'.join(groups['circle']) or '无需蹭圈',
    ]


def replay(roster, candidate):
    """Replay chronology; generated isolation blocks later buffs, not damage."""
    actions, guides = candidate["actions"], candidate["guides"]
    if len(actions) != 25 or len(guides) != 3 or len(set(guides)) != 3:
        raise ValueError("动作或引导数量错误。")
    if any(type(a) is not int or not 0 <= a < 8 for a in actions):
        raise ValueError("动作编码错误。")
    if any(actions[i] % 4 != 1 for i in guides):
        raise ValueError("引导只能吃自己的一个圈。")
    states = [BuffState(p["value"]) for p in roster]
    result = [
        dict(
            id="initial",
            title="当前",
            states=state_payload(roster, states),
            counts=totals(states),
            affected=[],
            events=[],
        )
    ]
    for stage in ("fan", "guides", "circle1", "circle2", "circle3", "settle"):
        if stage == "fan":
            targets = [i for i, a in enumerate(actions) if a // 4]
        elif stage.startswith("circle"):
            order = int(stage[-1])
            targets = [guides[order - 1]]
            targets += [
                i
                for i, a in enumerate(actions)
                if i not in guides
                and (order == 1 and a % 4 >= 1 or order == 2 and a % 4 == 3 or order == 3 and a % 4 >= 2)
            ]
        else:
            targets = []
        events = []
        for i in targets:
            before = states[i]
            event = receive_buff(before, "red" if stage == "fan" else "black")
            states[i] = event.state
            events.append(
                dict(
                    index=i,
                    before=before.value,
                    after=event.state.value,
                    applied=event.applied,
                    weak=event.weak,
                    strong=event.strong,
                    isolated=event.state.isolated,
                )
            )
        result.append(
            dict(
                id=stage,
                states=state_payload(roster, states),
                affected=targets,
                events=events,
                counts=totals(states),
            )
        )
    return result


def quota_group(roster, candidate, timeline):
    """One interchangeable Buff group may volunteer by count, without names.

    All ordinary actions must be identical and guides must take only their
    own hit. Swapping equal starting states then preserves every event. This
    deliberately excludes mixed fan/circle jobs and strong cancellations.
    """
    actions, guides = candidate["actions"], set(candidate["guides"])
    helpers = [i for i, action in enumerate(actions) if action and i not in guides]
    codes = {actions[i] for i in helpers}
    if len(codes) != 1 or any(actions[i] != 1 for i in guides):
        return None
    code = next(iter(codes))
    values = {roster[i]["value"] for i in helpers}
    if code not in (1, 2, 3, 4) or len(values) != 1:
        return None
    if any(event["strong"] and event["index"] in helpers for stage in timeline for event in stage["events"]):
        return None
    value = next(iter(values))
    eligible = [i for i, player in enumerate(roster) if i not in guides and player["value"] == value]
    return dict(code=code, selected=helpers, eligible=eligible, value=value)


def quota_selector(group):
    selected = group["selected"]
    return dict(
        kind="quota",
        label=LABELS[group["value"] + 3],
        selected=selected,
        eligible=group["eligible"],
        mask=mask_of(selected),
        target_mask=mask_of(selected),
        named=[],
        include=[],
        exclude=[],
        cost=1.0,
    )


def build_board(roster, candidate, *, tolerance=2):
    timeline = replay(roster, candidate)
    if abs(timeline[-1]["counts"]["difference"]) > tolerance:
        return None
    for stage in timeline:
        stage['counts']['balanced'] = abs(stage['counts']['difference']) <= tolerance
    actions, guides = candidate["actions"], candidate["guides"]
    full = (1 << 25) - 1
    ordinary = full & ~mask_of(guides)
    extras = set(candidate["extras"])
    if len(extras) > 5 or extras & set(guides):
        raise ValueError("额外点名超出上限。")
    allowed = mask_of(extras | set(guides))
    initial = [BuffState(p["value"]) for p in roster]
    after_fan = [BuffState(p["value"], p["isolated"]) for p in timeline[1]["states"]]
    quota = quota_group(roster, candidate, timeline)
    fan = (
        quota_selector(quota)
        if quota and quota["code"] == 4
        else compile_selector(
            roster, initial, full, mask_of(i for i, a in enumerate(actions) if a // 4), allowed, "所有人"
        )
    )
    if fan is None:
        return None
    cohorts = []
    for hits in range(1, 4):
        target = mask_of(i for i, a in enumerate(actions) if i not in guides and a % 4 == hits)
        selector = (
            quota_selector(quota)
            if quota and quota["code"] == hits
            else compile_selector(roster, after_fan, ordinary, target, allowed, "大团")
        )
        if selector is None:
            return None
        if target:
            cohorts.append(dict(hits=hits, reference='after_fan', selector=selector))
    strong = sum(e["strong"] for stage in timeline for e in stage["events"])
    named = set(fan["named"]) - set(guides)
    for cohort in cohorts:
        named.update(set(cohort["selector"]["named"]) - set(guides))
    if len(named) > 5:
        return None
    names = [p["name"] for p in roster]
    triangle = any(a % 4 == 3 for i, a in enumerate(actions) if i not in guides)
    fan_rows = []
    if fan["selected"]:
        fan_rows.append(compact_row(roster, initial, fan["selected"], "吃扇", "fan", fan))
    circle_rows = [compact_row(roster, after_fan, guides, "引导", "guide")]
    for cohort in cohorts:
        row = compact_row(
            roster,
            after_fan,
            cohort["selector"]["selected"],
            ("蹭圈", "蹭双圈", "蹭三圈")[cohort["hits"] - 1],
            "circle",
            cohort["selector"],
            'after_fan',
        )
        circle_rows.append(row)
    two = [c for c in cohorts if c["hits"] == 2]
    three = [c for c in cohorts if c["hits"] == 3]
    fan_line = "\n".join(row["text"] for row in fan_rows)
    circle_line = "\n".join(row["text"] for row in circle_rows)
    final = timeline[-1]["counts"]
    lines = {
        "initial": ("当前", ""),
        "fan": ("扇形后", ""),
        "guides": ("圈前", ""),
        "circle1": ("第一次闪现后", ""),
        "circle2": ("第二次闪现后", ""),
        "circle3": ("第三次闪现后", ""),
        "settle": ("结算", f"红 {final['red']}　黑 {final['black']}　差 {final['difference']:+d}　平衡"),
    }
    for stage in timeline:
        stage["title"], stage["call"] = lines[stage["id"]]
    # Exactly two spoken opportunities.  Every circle cohort is announced and
    # frozen before circle 1; circle 2/3 are numerical replay only, never calls.
    cues = [
        dict(
            id="before_fan",
            title="扇形前",
            call=fan_line,
            rows=fan_rows,
            states=timeline[0]["states"],
            counts=timeline[0]["counts"],
            affected=fan["selected"],
        ),
        dict(
            id="before_circles",
            title="圈前",
            call=circle_line,
            rows=circle_rows,
            states=timeline[2]["states"],
            counts=timeline[2]["counts"],
            affected=sorted(set(guides) | {i for c in cohorts for i in c["selector"]["selected"]}),
        ),
    ]
    score = fan["cost"] + sum(c["selector"]["cost"] for c in cohorts)
    score += int(bool(fan["selected"])) * 0.7 + bool(two) * 2.5 + bool(three) * 7
    score += len(named) * 0.2
    # No optional operation is the easiest instruction if mandatory guides
    # alone preserve balance. Names on guide placement are always separate.
    return dict(
        source=candidate["source"],
        actions=actions,
        guides=guides,
        extra_indices=sorted(named),
        extra_named_count=len(named),
        extra_named_budget=5,
        guide_named_count=3,
        guides_included=False,
        fan_selector=fan,
        cohorts=cohorts,
        cues=cues,
        timeline=timeline,
        initial=timeline[0]["counts"],
        final=final,
        strong_events=strong,
        speech_cost=round(score, 3),
        remembered_cohort=False,
        quota=quota,
        guide_roles=[
            dict(
                index=i,
                key=roster[i]["key"],
                name=names[i],
                order=order + 1,
                place=("1号点", "侧点" if triangle else "单走", "1号点旁边")[order],
            )
            for order, i in enumerate(guides)
        ],
        formation="三点成三角；引导互不吃到对方圈，双圈区与三圈交集分开。"
        if triangle
        else "1号点和旁点的圈可重叠；两位引导互不蹭圈，两圈组站交集。",
        prompt="\n".join(line for line in (fan_line, circle_line) if line),
    )


def participant_score(roster, board):
    actors = [i for i, action in enumerate(board["actions"]) if action]
    helpers = [i for i in actors if i not in board["guides"]]
    created = sum(
        player["value"] == 0 and final["value"] != 0
        for player, final in zip(roster, board["timeline"][-1]["states"])
    )
    return (
        board["strong_events"],
        sum(board['actions'][i] % 4 == 3 for i in helpers),
        sum(board['actions'][i] % 4 == 2 for i in helpers),
        sum(roster[i]["team"] == 5 for i in actors),
        len(helpers),
        sum(roster[i]["team"] in (3, 4) for i in actors),
        abs(board["final"]["difference"]),
        created,
        sum(action // 4 + action % 4 for action in board["actions"]),
        tuple(actors),
        board["speech_cost"],
        board["extra_named_count"],
        tuple(board["guides"]),
    )


def quota_variants(roster, board, *, tolerance=2):
    """Replay each valid volunteer count with fixed, excluded guides."""
    group = board["quota"]
    if not group:
        return []
    variants = []
    fifth_limit = participant_score(roster, board)[3]
    for count in range(1, len(group["eligible"]) + 1):
        actions = [0] * 25
        for index in board["guides"]:
            actions[index] = 1
        for index in group["eligible"][:count]:
            actions[index] = group["code"]
        candidate = dict(actions=actions, guides=board["guides"], extras=[], source=board["source"])
        try:
            alternative = build_board(roster, candidate, tolerance=tolerance)
        except IllegalAction:
            continue
        if alternative is None or participant_score(roster, alternative)[3] > fifth_limit:
            continue
        variants.append(dict(count=count, board=alternative))
    if not variants:
        return []
    counts = [variant["count"] for variant in variants]
    preferred = min(
        variants, key=lambda variant: (abs(variant["board"]["final"]["difference"]), variant["count"])
    )
    balanced_count = preferred["count"] if preferred["board"]["final"]["difference"] == 0 else None
    for option in [board, *(variant["board"] for variant in variants)]:
        row = next(row for cue in option["cues"] for row in cue["rows"] if row.get("quota"))
        row["minimum_count"], row["maximum_count"] = min(counts), max(counts)
        row["balanced_count"] = balanced_count
        if len(counts) > 1:
            row["count_text"] = (
                f"{min(counts)}~{max(counts)}人"
                if counts == list(range(min(counts), max(counts) + 1))
                else "/".join(map(str, counts)) + "人"
            )
        row["text"] = compact_text(row)
        for cue in option["cues"]:
            cue["call"] = "\n".join(row["text"] for row in cue["rows"])
        option["prompt"] = "\n".join(cue["call"] for cue in option["cues"] if cue["call"])
    return variants


def team_variants(roster, board, *, tolerance=2):
    """Verify interchangeable full squads, relocating the three guides too."""
    if board["fan_selector"]["selected"] or len(board["cohorts"]) != 1 or board["extra_named_count"]:
        return []
    cohort = board["cohorts"][0]
    selected = cohort["selector"]["selected"]
    selected_teams = {roster[i]["team"] for i in selected}
    selected_values = {roster[i]["value"] for i in selected}
    guide_values = {roster[i]["value"] for i in board["guides"]}
    if len(selected) != 5 or len(selected_teams) != 1 or len(selected_values) != 1 or len(guide_values) != 1:
        return []
    target_value = next(iter(selected_values))
    guide_value = next(iter(guide_values))
    variants = []
    for team in range(1, 6):
        targets = [i for i, p in enumerate(roster) if p["team"] == team]
        if any(roster[i]["value"] != target_value for i in targets):
            continue
        guides = [i for i, p in enumerate(roster) if i not in targets and p["value"] == guide_value][:3]
        if len(guides) < 3:
            continue
        actions = [0] * 25
        for index in targets:
            actions[index] = cohort["hits"]
        for index in guides:
            actions[index] = 1
        candidate = dict(actions=actions, guides=guides, extras=[], source=board["source"])
        alternative = build_board(roster, candidate, tolerance=tolerance)
        if (
            alternative is not None
            and alternative["strong_events"] <= board["strong_events"]
            and alternative["final"] == board["final"]
        ):
            variants.append(dict(team=team, board=alternative))
    if len(variants) < 2:
        return []
    choices = [variant["team"] for variant in variants]
    for option in [board, *(variant["board"] for variant in variants)]:
        row = option["cues"][1]["rows"][1]
        row["team_choices"] = choices
        row["text"] = compact_text(row)
        option["cues"][1]["call"] = "\n".join(row["text"] for row in option["cues"][1]["rows"])
        option["prompt"] = option["cues"][1]["call"]
    return variants


def solve_steps(roster, mode, catalogue, *, tolerance=2):
    started = perf_counter()
    if mode not in ("all", "circle"):
        raise ValueError("仅支持扇＋圈、仅圈。")
    roster = validate_roster(roster)
    fallback = catalogue_candidate(roster, mode, catalogue)
    # The existing histogram oracle provides the mathematical impossibility
    # check under precisely this experiment's seven-state / alive-only scope.
    if fallback is None:
        return dict(
            status="impossible",
            prompt="无解，等死吧",
            roster=roster,
            mode=mode,
            timeline=[],
            elapsed_ms=round((perf_counter() - started) * 1000, 1),
        )
    neutral_guides = [i for i, player in enumerate(roster) if player["value"] < 0][:3]
    initial_difference = sum(player["value"] > 0 for player in roster) - sum(
        player["value"] < 0 for player in roster
    )
    if abs(initial_difference) <= tolerance and len(neutral_guides) == 3:
        actions = [int(i in neutral_guides) for i in range(25)]
        candidates = [dict(actions=actions, guides=neutral_guides, extras=[], source="balanced")]
    else:
        candidates = list(squad_candidates(roster, mode, tolerance))
        candidates.append(fallback)
    best = None
    checked = 0
    for candidate in candidates:
        try:
            board = build_board(roster, candidate, tolerance=tolerance)
        except IllegalAction:
            continue
        if board is None:
            continue
        checked += 1
        score = participant_score(roster, board)
        if best is None or score < best[0]:
            best = (score, board)
    if best is None:
        return dict(
            status="complex",
            prompt="有数学解；当前未找到五人额外点名内的分步口令。",
            roster=roster,
            mode=mode,
            timeline=[],
            elapsed_ms=round((perf_counter() - started) * 1000, 1),
        )
    board = best[1]
    counts = quota_variants(roster, board, tolerance=tolerance)
    variants = [] if counts else team_variants(roster, board, tolerance=tolerance)
    return dict(
        status="solved",
        mode=mode,
        roster=roster,
        **board,
        team_variants=variants,
        quota_variants=counts,
        candidates_checked=checked,
        elapsed_ms=round((perf_counter() - started) * 1000, 1),
        scope="当前Buff；不推定T加三层。引导顺序需按实际点名核对，未自动识别人名或注视顺序。",
    )
