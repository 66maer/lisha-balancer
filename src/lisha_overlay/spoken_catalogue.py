"""Whole-raid calls with separate guide and additional-player budgets."""

import json
from pathlib import Path
import numpy as np

FAMILIES = (
    "大团原地，少数处理",
    "大团一起吃扇",
    "大团主点抱团",
    "大团主点到旁点",
    "按Buff分组",
    "三圈重叠",
    "未找到点名上限内口令",
    "模型内无解",
)
LABELS = ("黑三", "黑二", "黑一", "无Buff", "红一", "红二", "红三")
ALIASES = "甲乙丙丁戊己庚辛"


def action_text(fan, hits):
    place = ("原地避圈", "去主点", "主点出圈后去旁点", "去三圈重叠点")[hits]
    return ("吃扇；" if fan else "") + place


def guide_roles(counts, fans):
    roles = []
    for state, (count, fan) in enumerate(zip(counts, fans)):
        for index in range(count):
            roles.append(
                dict(alias=ALIASES[len(roles)], buff=LABELS[state], state=state - 3, fan=index < fan, hits=1)
            )
    assert len(roles) == 3
    for role, place in zip(roles, ("主点", "单走", "主点旁")):
        role["place"] = place
    return roles


def named_join(people):
    return "、".join(p["alias"] for p in people)


def guide_call(roles, triangle=False):
    return "，".join(
        p["alias"]
        + (
            "去侧点"
            if triangle and p["place"] == "单走"
            else "单走"
            if p["place"] == "单走"
            else "去" + p["place"]
        )
        for p in roles
    )


class SpokenCatalogue:
    def __init__(self, old, base):
        self.old = old
        self.base = Path(base)
        self.counts = old.counts
        pointer = self.base / "active-spoken.json"
        directory = (
            json.loads(pointer.read_text(encoding="utf8"))["directory"] if pointer.exists() else "spoken"
        )
        self.directory = self.base / directory
        if self.directory.resolve().parent != self.base.resolve():
            raise ValueError("Invalid catalogue directory")
        self.summary = json.loads((self.directory / "summary.json").read_text(encoding="utf8"))
        self.extra_budget = int(self.summary["additional_named_budget"])
        self.total_budget = 3 + self.extra_budget
        self.guides_included = bool(self.summary["guides_included"])
        self.summary["modes"] = {
            mode: {**info, "families": {name: n for name, n in zip(FAMILIES, info["families"].values())}}
            for mode, info in self.summary["modes"].items()
        }
        bulk_directory = Path(self.summary["bulk_directory"])
        if not bulk_directory.is_absolute():
            bulk_directory = self.base / bulk_directory
        with np.load(bulk_directory / "profiles.npz") as f:
            self.profiles = f["guides"]
            self.profile_fans = f["fan"]
        self.tables = {}
        self.bulk = {}
        self.extra = {}
        self.mixed = {}
        for mode in ("all", "circle"):
            for destination, path in (
                (self.tables, self.directory / f"{mode}.npz"),
                (self.bulk, bulk_directory / f"{mode}.npz"),
                (self.extra, self.directory / f"{mode}-extra.npz"),
                (self.mixed, self.directory / f"{mode}-mixed.npz"),
            ):
                with np.load(path) as f:
                    destination[mode] = {key: f[key] for key in f.files}

    def solve(self, counts, mode):
        old = self.old.solve(counts, mode)
        index = old["index"]
        selected = self.tables[mode]
        source = int(selected["source"][index])
        family = int(selected["family"][index])
        result = dict(
            index=index,
            counts=counts,
            mode=mode,
            family=FAMILIES[family],
            family_id=family,
            initial_difference=old["initial_difference"],
            named_budget=5,
            guides_included=self.guides_included,
            extra_named_budget=self.extra_budget,
            maximum_total_named=self.total_budget,
            guide_named_count=3,
        )
        if source == 0:
            return dict(
                **result,
                source="none",
                level=255,
                final_difference=None,
                named_count=0,
                exception_count=0,
                exceptions=[],
                rows=[],
                prompt="无解，等死吧" if family == 7 else "有数学解；当前未找到点名上限内的简短口令",
                extra_named_count=0,
                guide_text="—",
                guide_prompt="",
                fan_prompt="",
                body_prompt="",
                formation="",
                strong_events=0,
            )
        if source == 1:
            return self.bulk_solve(result, index, mode)
        if source == 2:
            guide = old["guide_counts"]
            quota = old["quota"]
            final = old["final_difference"]
            level = old["level"]
            fans = [0] * 7
        elif source == 3:
            table = self.extra[mode]
            at = int(np.searchsorted(table["index"], index))
            guide = self.old.guides[table["guide"][at]].tolist()
            quota = table["quota"][at].tolist()
            final = int(table["final"][at])
            level = int(table["level"][at])
            fans = [0] * 7
        else:
            table = self.mixed[mode]
            at = int(np.searchsorted(table["index"], index))
            profile = int(table["profile"][at])
            guide = self.profiles[0, profile].tolist()
            fans = self.profile_fans[0, profile].tolist()
            quota = table["quota"][at].tolist()
            final = int(table["final"][at])
            level = int(table["level"][at])
        # The old renderer already merges common actions and names only the
        # minority. Shift its placeholders past the three named guides.
        from .strategy_catalogue import describe

        detail = describe(counts, guide, quota)
        translation = str.maketrans({a: b for a, b in zip("甲乙丙丁戊", "丁戊己庚辛")})
        assert detail["exception_count"] <= self.extra_budget
        for item in detail["exceptions"]:
            item["alias"] = item["alias"].translate(translation)
        prompt = detail["prompt"].translate(translation)
        roles = guide_roles(guide, fans)
        guide_prompt = guide_call(roles, level == 3)
        guide_fan = [p for p in roles if p["fan"]]
        fan_prompt = named_join(guide_fan) + "吃扇；其余按Buff口令" if guide_fan else ""
        return dict(
            **result,
            **{k: v for k, v in detail.items() if k != "prompt"},
            source="buff",
            level=level,
            final_difference=final,
            named_count=3 + detail["exception_count"],
            extra_named_count=detail["exception_count"],
            guide_counts=guide,
            guide_fans=fans,
            quota=quota,
            guide_roles=roles,
            prompt=prompt,
            body_prompt=prompt,
            guide_prompt=guide_prompt,
            fan_prompt=fan_prompt,
            strong_events=guide[6],
            formation="主、旁、侧三点成三角；双圈区和三圈交集分开"
            if level == 3
            else "主点＝1号点；旁点在附近，单走者避开两点",
            has_extra_actions=any(quota) or any(fans),
        )

    def bulk_solve(self, result, index, mode):
        table = self.bulk[mode]
        action = int(table["baseline"][index])
        profile = int(table["profile"][index])
        fan = action // 4
        hits = action % 4
        g = self.profiles[fan, profile].tolist()
        gf = self.profile_fans[fan, profile].tolist()
        roles = guide_roles(g, gf)
        exceptions = []
        for field in (f"exception{i}" for i in range(1, self.extra_budget + 1)):
            encoded = int(table[field][index])
            if encoded >= 56:
                continue
            state, alternative = divmod(encoded, 8)
            exceptions.append(
                dict(
                    alias=ALIASES[3 + len(exceptions)],
                    buff=LABELS[state],
                    state=state - 3,
                    fan=bool(alternative // 4),
                    hits=alternative % 4,
                    action=action_text(alternative // 4, alternative % 4),
                )
            )
        named = roles + exceptions
        if fan:
            exclusions = [p for p in named if not p["fan"]]
            if all(p in exclusions for p in roles):
                other = [p for p in exclusions if p not in roles]
                excluded = "三位引导" + ("、" + named_join(other) if other else "")
            else:
                excluded = named_join(exclusions)
            fan_prompt = "全团吃扇" + ("，" + excluded + "除外" if exclusions else "")
        else:
            eat = [p for p in named if p["fan"]]
            fan_prompt = named_join(eat) + "吃扇，大团其余不吃" if eat else ""
        maximum = int(table["limit"][index])
        triangle = maximum == 3
        guide_prompt = guide_call(roles, triangle)
        body_prompt = (
            "大团原地抱团避圈",
            "大团去主点抱团",
            "大团去双圈重叠点" if triangle else "大团主点抱团，出圈后一起去旁点",
            "大团去三圈重叠点",
        )[hits]
        circle_changes = {}
        for extra in exceptions:
            if extra["hits"] == hits:
                continue
            place = ("避圈", "只去主点", "去双圈重叠点" if triangle else "主点出圈后去旁点", "去三圈重叠点")[
                extra["hits"]
            ]
            circle_changes.setdefault(place, []).append(extra["alias"])
        for place, aliases in circle_changes.items():
            body_prompt += "；" + "、".join(aliases) + place
        rows = []
        for state, label in enumerate(LABELS):
            if not result["counts"][state]:
                continue
            rows.append(
                dict(
                    buff=label,
                    total=result["counts"][state],
                    guides=g[state],
                    normal=action_text(fan, hits),
                    exceptions=[e for e in exceptions if e["state"] == state - 3],
                )
            )
        prompt = "；".join(part for part in (fan_prompt, body_prompt) if part)
        return dict(
            **result,
            source="bulk",
            level=maximum,
            baseline=action,
            body_fan=bool(fan),
            body_hits=hits,
            final_difference=int(table["final"][index]),
            named_count=len(named),
            extra_named_count=len(exceptions),
            exception_count=len(exceptions),
            exceptions=exceptions,
            guide_counts=g,
            guide_fans=gf,
            guide_roles=roles,
            guide_prompt=guide_prompt,
            fan_prompt=fan_prompt,
            body_prompt=body_prompt,
            prompt=prompt,
            guide_text="、".join(f"{n}{LABELS[i]}" for i, n in enumerate(g) if n) + "引导",
            rows=rows,
            strong_events=int(table["strong"][index]),
            formation="主、旁、侧三点成三角；双圈区和三圈交集分开"
            if triangle
            else "主点＝1号点；旁点在附近，单走者避开两点",
            has_extra_actions=bool(fan or hits or exceptions or any(gf)),
        )
