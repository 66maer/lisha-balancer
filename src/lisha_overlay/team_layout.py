"""Calibratable, normalized positions for the game's five-by-five team panel.

Coordinates passed to ``cells`` describe captured content, excluding the scan
window's border. Group rectangles are normalized within that content; the three
sub-rectangles are normalized within each player's cell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Literal

from PySide6.QtCore import QRectF, QSize, QSizeF

NormalizedRect = tuple[float, float, float, float]


def _normalized_rect(value: Any, label: str) -> NormalizedRect:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{label} 应包含 x、y、width、height 四个比例。")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError(f"{label} 的比例必须是有限数字。")
    try:
        x, y, width, height = (float(item) for item in value)
    except OverflowError as error:
        raise ValueError(f"{label} 的比例超出有限数字范围。") from error
    if (
        not all(math.isfinite(item) for item in (x, y, width, height))
        or min(x, y) < 0 or min(width, height) <= 0
        or x + width > 1.000000001 or y + height > 1.000000001
    ):
        raise ValueError(f"{label} 必须完整落在 0～1 范围内。")
    return x, y, width, height


def _project(parent: QRectF, normalized: NormalizedRect) -> QRectF:
    x, y, width, height = normalized
    return QRectF(
        parent.x() + x * parent.width(), parent.y() + y * parent.height(),
        width * parent.width(), height * parent.height(),
    )


@dataclass(frozen=True)
class PlayerCell:
    key: str
    group: int
    slot: int
    rect: QRectF
    name_rect: QRectF
    buff_rect: QRectF
    marker_rect: QRectF

    @property
    def id(self) -> str:
        return self.key


@dataclass(frozen=True)
class GroupGridLayout:
    group: int
    rect: NormalizedRect

    def __post_init__(self) -> None:
        if isinstance(self.group, bool) or not isinstance(self.group, int) or not 1 <= self.group <= 8:
            raise ValueError("队伍编号必须是 1～8。")
        object.__setattr__(self, "rect", _normalized_rect(self.rect, "队伍区域"))


def _default_grid_options() -> dict[str, Any]:
    return {
        "formation": (3, 2),
        "left": 0.045, "top": 0.045, "right": 0.01, "bottom": 0.01,
        "column_gap": 0.005, "row_gap": 0.025, "footer": 0.025,
    }


def _grid_options(options: dict[str, Any] | None) -> dict[str, Any]:
    if options is not None and not isinstance(options, dict):
        raise ValueError("网格参数必须是对象。")
    defaults = _default_grid_options()
    if options and set(options) - set(defaults):
        raise ValueError("网格配置含有不支持的参数。")
    result = {**defaults, **(options or {})}
    formation = result["formation"]
    if isinstance(formation, int) and not isinstance(formation, bool):
        formation = (formation,)
    if (
        not isinstance(formation, (list, tuple)) or not formation
        or any(isinstance(count, bool) or not isinstance(count, int) or count <= 0
               for count in formation)
        or not 1 <= sum(formation) <= 8
    ):
        raise ValueError("每行列数应为正整数，总列数应在 1～8 之间。")
    result["formation"] = tuple(formation)
    for key in defaults.keys() - {"formation"}:
        value = result[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not 0 <= value < 1):
            raise ValueError(f"{key} 必须是 0～1 之间的有限比例。")
        result[key] = float(value)
    max_columns = max(formation)
    rows = len(formation)
    available_width = 1 - result["left"] - result["right"] - (max_columns - 1) * result["column_gap"]
    block_height = (
        1 - result["top"] - result["bottom"] - (rows - 1) * result["row_gap"]
    ) / rows
    if available_width <= 0 or block_height <= result["footer"]:
        raise ValueError("边距、间距和队名区域占用过大，已没有可用的玩家区域。")
    return result


def _groups_from_grid(options: dict[str, Any]) -> tuple[GroupGridLayout, ...]:
    formation = options["formation"]
    width = (1 - options["left"] - options["right"]
             - (max(formation) - 1) * options["column_gap"]) / max(formation)
    block_height = (1 - options["top"] - options["bottom"]
                    - (len(formation) - 1) * options["row_gap"]) / len(formation)
    player_height = block_height - options["footer"]
    groups = []
    for row, count in enumerate(formation):
        for column in range(count):
            groups.append(GroupGridLayout(
                len(groups) + 1,
                (options["left"] + column * (width + options["column_gap"]),
                 options["top"] + row * (block_height + options["row_gap"]),
                 width, player_height),
            ))
    return tuple(groups)


@dataclass(frozen=True)
class TeamLayout:
    groups: tuple[GroupGridLayout, ...] = ()
    name_rect: NormalizedRect = (0.16, 0.025, 0.82, 0.36)
    buff_rect: NormalizedRect = (0.015, 0.43, 0.43, 0.53)
    marker_rect: NormalizedRect = (0.64, 0.43, 0.34, 0.53)
    grid_options: dict[str, Any] = field(default_factory=_default_grid_options)

    def __post_init__(self) -> None:
        options = _grid_options(self.grid_options)
        groups = tuple(self.groups) if self.groups else _groups_from_grid(options)
        if (not 1 <= len(groups) <= 8
                or any(not isinstance(group, GroupGridLayout) for group in groups)
                or sorted(group.group for group in groups) != list(range(1, len(groups) + 1))):
            raise ValueError("布局应包含连续编号的 1～8 个队伍，每队一个区域。")
        if len(groups) != sum(options["formation"]):
            if options == _default_grid_options():
                options = _grid_options({"formation": (len(groups),)})
            else:
                raise ValueError("队伍区域数量与网格总列数不一致。")
        object.__setattr__(self, "grid_options", options)
        object.__setattr__(self, "groups", tuple(sorted(groups, key=lambda group: group.group)))
        for key in ("name_rect", "buff_rect", "marker_rect"):
            object.__setattr__(self, key, _normalized_rect(getattr(self, key), key))

    @classmethod
    def uniform(
        cls, formation: tuple[int, ...] = (3, 2), *,
        left: float = 0.045, top: float = 0.045, right: float = 0.01,
        bottom: float = 0.01, column_gap: float = 0.005,
        row_gap: float = 0.025, footer: float = 0.025,
        name_rect: NormalizedRect = (0.16, 0.025, 0.82, 0.36),
        buff_rect: NormalizedRect = (0.015, 0.43, 0.43, 0.53),
        marker_rect: NormalizedRect = (0.64, 0.43, 0.34, 0.53),
    ) -> "TeamLayout":
        """Build uniformly sized five-player columns in any 1–8-column formation.

        Margins, gaps, and ``footer`` are fractions of the complete captured
        content. Short block rows align to the left and keep the same column
        width. A footer is reserved below every column for its team label.
        """
        options = _grid_options({
            "formation": formation, "left": left, "top": top, "right": right,
            "bottom": bottom, "column_gap": column_gap, "row_gap": row_gap,
            "footer": footer,
        })
        return cls(groups=_groups_from_grid(options), name_rect=name_rect,
                   buff_rect=buff_rect, marker_rect=marker_rect, grid_options=options)

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None) -> "TeamLayout":
        if config is None:
            return cls()
        if not isinstance(config, dict):
            raise ValueError("团队布局配置必须是对象。")
        if not config:
            return cls()
        raw_groups = config.get("groups")
        options = _grid_options(config.get("grid_options"))
        if raw_groups is None:
            groups = _groups_from_grid(options)
        else:
            if not isinstance(raw_groups, list) or not 1 <= len(raw_groups) <= 8:
                raise ValueError("布局应包含 1～8 个队伍区域。")
            try:
                groups = tuple(GroupGridLayout(item["group"], item["rect"]) for item in raw_groups)
            except (KeyError, TypeError) as error:
                raise ValueError("每个队伍区域都应包含 group 和 rect。") from error
        defaults = cls()
        return cls(
            groups=groups,
            grid_options=options,
            **{
                key: _normalized_rect(config.get(key, getattr(defaults, key)), key)
                for key in ("name_rect", "buff_rect", "marker_rect")
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "groups": [{"group": group.group, "rect": list(group.rect)} for group in self.groups],
            "name_rect": list(self.name_rect),
            "buff_rect": list(self.buff_rect),
            "marker_rect": list(self.marker_rect),
            "grid_options": {
                **self.grid_options, "formation": list(self.grid_options["formation"])
            },
        }

    def cells(self, content: QRectF | QSize | QSizeF) -> list[PlayerCell]:
        if isinstance(content, (QSize, QSizeF)):
            area = QRectF(0, 0, content.width(), content.height())
        else:
            area = QRectF(content)
        if area.isEmpty() or not all(math.isfinite(value) for value in (
            area.x(), area.y(), area.width(), area.height()
        )):
            raise ValueError("团队内容区域必须具有有效的正数尺寸。")
        result = []
        for group in self.groups:
            group_area = _project(area, group.rect)
            height = group_area.height() / 5
            for slot in range(1, 6):
                cell = QRectF(group_area.x(), group_area.y() + (slot - 1) * height,
                              group_area.width(), height)
                result.append(PlayerCell(
                    key=f"g{group.group}p{slot}", group=group.group, slot=slot, rect=cell,
                    name_rect=_project(cell, self.name_rect),
                    buff_rect=_project(cell, self.buff_rect),
                    marker_rect=_project(cell, self.marker_rect),
                ))
        return result


@dataclass(frozen=True)
class Annotation:
    player_key: str
    kind: Literal["fan", "circle"]
    order: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.player_key, str) or self.player_key not in {
            f"g{group}p{slot}" for group in range(1, 9) for slot in range(1, 6)
        }:
            raise ValueError("标记必须指定有效的队伍与玩家格子。")
        if self.kind not in ("fan", "circle"):
            raise ValueError("标记类型必须是 fan 或 circle。")
        if isinstance(self.order, bool) or not isinstance(self.order, int) or not 1 <= self.order <= 3:
            raise ValueError("黑圈顺序必须是 1、2 或 3。")
