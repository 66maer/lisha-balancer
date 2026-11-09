"""Verified histogram catalogue used by the live and offline planners."""

import json, math
import numpy as np

LABELS = ("黑三", "黑二", "黑一", "无Buff", "红一", "红二", "红三")
FAMILIES = (
    "仅三点引导",
    "黑一吃扇消掉",
    "无Buff吃扇补红",
    "黑一与无Buff吃扇",
    "红一进一圈消掉",
    "无Buff进一圈补黑",
    "红一与无Buff进一圈",
    "两圈修正",
    "三圈修正",
    "无解",
)
VERBS = {"wait": "不动", "fan": "吃扇", "one": "去主点", "two": "主点出圈后去旁点", "three": "三圈重叠"}
ALIASES = "甲乙丙丁戊己庚辛壬癸"


def rank_histogram(counts):
    remaining = 25
    parts = 7
    rank = 0
    for value in counts[:-1]:
        rank += math.comb(remaining + parts - 1, parts - 1) - math.comb(
            remaining - value + parts - 1, parts - 1
        )
        remaining -= value
        parts -= 1
    return rank


def describe(counts, guide, quota):
    helpers = [n - g for n, g in zip(counts, guide)]
    behaviors = {i: {"wait": n} for i, n in enumerate(helpers)}
    for state, action, column in (
        (2, "fan", 0),
        (3, "fan", 1),
        (3, "one", 2),
        (4, "one", 3),
        (4, "two", 4),
        (5, "two", 5),
        (5, "three", 6),
    ):
        n = quota[column]
        if n:
            behaviors[state][action] = n
            behaviors[state]["wait"] -= n
    normal = {}
    exceptions = []
    rows = []
    name_index = 0
    for state, population in behaviors.items():
        nonzero = {key: n for key, n in population.items() if n}
        if not nonzero:
            if counts[state]:
                rows.append(
                    dict(
                        buff=LABELS[state],
                        total=counts[state],
                        guides=guide[state],
                        normal="—",
                        exceptions=[],
                    )
                )
            continue
        majority = max(nonzero, key=lambda a: (nonzero[a], a == "wait"))
        special = []
        excluded = []
        for action, n in nonzero.items():
            if action == majority:
                continue
            for _ in range(n):
                alias = ALIASES[name_index] if name_index < len(ALIASES) else f"补位{name_index + 1}"
                name_index += 1
                item = dict(alias=alias, buff=LABELS[state], action=VERBS[action], code=action)
                special.append(item)
                exceptions.append(item)
                if action == "wait":
                    excluded.append(alias)
        if majority != "wait":
            selector = LABELS[state] + ("除" + "、".join(excluded) if excluded else "")
            normal.setdefault(majority, []).append(selector)
        rows.append(
            dict(
                buff=LABELS[state],
                total=counts[state],
                guides=guide[state],
                normal=VERBS[majority],
                exceptions=special,
            )
        )
    clauses = ["、".join(selectors) + VERBS[action] for action, selectors in normal.items()]
    named_groups = {}
    for item in exceptions:
        if item["code"] == "wait":
            continue
        group_majority = next(r["normal"] for r in rows if r["buff"] == item["buff"])
        named_groups.setdefault((item["action"], group_majority != "不动"), []).append(item["alias"])
    for (action, override), aliases in named_groups.items():
        clauses.append("、".join(aliases) + ("改" if override else "") + action)
    guide_text = "、".join(f"{n}{LABELS[i]}" for i, n in enumerate(guide) if n) + "引导"
    prompt = "；".join(clauses) if clauses else guide_text
    prompt += "；其余避圈"
    return dict(
        prompt=prompt,
        guide_text=guide_text,
        exceptions=exceptions,
        exception_count=len(exceptions),
        rows=rows,
    )


class Catalogue:
    def __init__(self, base):
        self.base = base
        self.counts = np.load(base / "full/histograms.npy", mmap_mode="r")
        self.guides = np.load(base / "full/guides.npy")
        self.tables = {}
        self.summary = json.loads((base / "phrases/summary.json").read_text(encoding="utf8"))
        for mode in ("all", "circle"):
            with np.load(base / f"phrases/{mode}.npz") as source:
                self.tables[mode] = {
                    key: source[key] for key in ("level", "guide", "quota", "final", "family")
                }

    def solve(self, counts, mode):
        if (
            mode not in self.tables
            or len(counts) != 7
            or any(
                isinstance(n, bool) or not isinstance(n, (int, np.integer)) or not 0 <= n <= 25
                for n in counts
            )
            or sum(counts) != 25
        ):
            raise ValueError("七种人数之和必须等于25。")
        index = rank_histogram(counts)
        assert self.counts[index].tolist() == counts
        table = self.tables[mode]
        level = int(table["level"][index])
        family = int(table["family"][index])
        current = sum(counts[4:]) - sum(counts[:3])
        result = dict(
            index=index,
            counts=counts,
            mode=mode,
            level=level,
            family=FAMILIES[family],
            initial_difference=current,
        )
        if level == 255:
            return dict(
                **result,
                prompt="无解，等死吧",
                final_difference=None,
                guide_text="—",
                exceptions=[],
                exception_count=0,
                rows=[],
            )
        guide = self.guides[table["guide"][index]].tolist()
        quota = table["quota"][index].tolist()
        return dict(
            **result,
            **describe(counts, guide, quota),
            guide_counts=guide,
            quota=quota,
            final_difference=int(table["final"][index]),
            strong_events=guide[6],
            formation="三角形：两圈区与三圈公共交集分开" if level == 3 else "1定点、2单独、3在1旁另一点",
        )
