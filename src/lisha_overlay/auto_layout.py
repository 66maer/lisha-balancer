"""Map detected physical pixels to a scanner without scaling monitor origins."""
from __future__ import annotations

import math

from PySide6.QtCore import QRect, QRectF

from .capture import normalize_region
from .team_layout import GroupGridLayout, TeamLayout


def scan_geometry_for_bounds(bounds: QRectF, monitor: QRect, dpr: float, *,
                             inset: int = 4, padding: int = 8) -> QRect:
    """Bounds are pixels relative to the captured monitor, not global pixels."""
    if (bounds.isEmpty() or monitor.isEmpty() or not math.isfinite(dpr) or dpr <= 0
            or not all(math.isfinite(v) for v in (bounds.x(), bounds.y(), bounds.width(), bounds.height()))):
        raise ValueError("无法换算识别区域。")
    if (bounds.left() < 0 or bounds.top() < 0
            or bounds.right() > round(monitor.width() * dpr) + 1
            or bounds.bottom() > round(monitor.height() * dpr) + 1):
        raise ValueError("识别区域超出显示器。")
    left = monitor.x() + math.floor(bounds.left() / dpr) - inset - padding
    top = monitor.y() + math.floor(bounds.top() / dpr) - inset - padding
    right = monitor.x() + math.ceil(bounds.right() / dpr) + inset + padding
    bottom = monitor.y() + math.ceil(bounds.bottom() / dpr) + inset + padding
    # The transparent outer border may extend offscreen. Its captured interior
    # must remain on this monitor, including grids right against a screen edge.
    allowed = monitor.adjusted(-inset, -inset, inset, inset)
    width = min(allowed.width(), max(120, right - left))
    height = min(allowed.height(), max(120, bottom - top))
    left = max(allowed.left(), min(left, allowed.x() + allowed.width() - width))
    top = max(allowed.top(), min(top, allowed.y() + allowed.height() - height))
    return QRect(left, top, width, height)


def rebase_detected_layout(layout: TeamLayout, source_region: dict, target_region: dict) -> TeamLayout:
    """Preserve every detected column after cropping to the actual native ROI."""
    source = normalize_region(source_region)
    target = normalize_region(target_region)
    groups = []
    for group in layout.groups:
        x, y, width, height = group.rect
        rect = (
            (source["left"] + x * source["width"] - target["left"]) / target["width"],
            (source["top"] + y * source["height"] - target["top"]) / target["height"],
            width * source["width"] / target["width"],
            height * source["height"] / target["height"],
        )
        rx, ry, rw, rh = rect
        if min(rx, ry) < -1e-6 or rx + rw > 1.000001 or ry + rh > 1.000001:
            raise ValueError("扫描区域未完整包含识别格子，请重试定位。")
        rx, ry = max(0., rx), max(0., ry)
        groups.append(GroupGridLayout(group.group, (rx, ry, min(rw, 1-rx), min(rh, 1-ry))))
    options = {
        "formation": layout.grid_options["formation"],
        "left": min(group.rect[0] for group in groups),
        "top": min(group.rect[1] for group in groups),
        "right": max(0., 1-max(group.rect[0]+group.rect[2] for group in groups)),
        "bottom": max(0., 1-max(group.rect[1]+group.rect[3] for group in groups)),
        "column_gap": 0., "row_gap": 0., "footer": 0.,
    }
    return TeamLayout(groups=tuple(groups), grid_options=options,
                      name_rect=layout.name_rect, buff_rect=layout.buff_rect, marker_rect=layout.marker_rect)
