"""Win32 overlay helpers and mixed-DPI, single-monitor region conversion."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

_IS_WINDOWS = sys.platform == "win32"
_USER32: Any = None
_REMOTE_COMPATIBLE = True

if _IS_WINDOWS:
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
    _USER32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _USER32.GetClientRect.restype = wintypes.BOOL
    _USER32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _USER32.ClientToScreen.restype = wintypes.BOOL
    _USER32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _USER32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    _USER32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _USER32.RegisterHotKey.restype = wintypes.BOOL
    _USER32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _USER32.UnregisterHotKey.restype = wintypes.BOOL
    _USER32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _USER32.SetWindowPos.restype = wintypes.BOOL

    _GET_WINDOW_LONG = getattr(_USER32, "GetWindowLongPtrW", _USER32.GetWindowLongW)
    _SET_WINDOW_LONG = getattr(_USER32, "SetWindowLongPtrW", _USER32.SetWindowLongW)
    _GET_WINDOW_LONG.argtypes = [wintypes.HWND, ctypes.c_int]
    _GET_WINDOW_LONG.restype = ctypes.c_ssize_t
    _SET_WINDOW_LONG.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _SET_WINDOW_LONG.restype = ctypes.c_ssize_t

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    _MONITOR_CALLBACK = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )
    _USER32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC, ctypes.POINTER(wintypes.RECT), _MONITOR_CALLBACK, wintypes.LPARAM,
    ]
    _USER32.EnumDisplayMonitors.restype = wintypes.BOOL
    _USER32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
    _USER32.GetMonitorInfoW.restype = wintypes.BOOL


def _native_monitors() -> dict[str, dict[str, int]]:
    if not _IS_WINDOWS:
        return {}
    monitors: dict[str, dict[str, int]] = {}

    @_MONITOR_CALLBACK
    def collect(handle: Any, _dc: Any, _rect: Any, _data: Any) -> bool:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if _USER32.GetMonitorInfoW(handle, ctypes.byref(info)):
            rect = info.rcMonitor
            monitors[info.szDevice.casefold()] = {
                "left": rect.left,
                "top": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            }
        return True

    if not _USER32.EnumDisplayMonitors(None, None, collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return monitors


def _is_contained(region: dict[str, int], monitor: dict[str, int]) -> bool:
    return (
        region["left"] >= monitor["left"]
        and region["top"] >= monitor["top"]
        and region["left"] + region["width"] <= monitor["left"] + monitor["width"]
        and region["top"] + region["height"] <= monitor["top"] + monitor["height"]
    )


def _require_single_monitor(region: dict[str, int]) -> dict[str, int]:
    if region["width"] <= 0 or region["height"] <= 0:
        raise ValueError("扫描区域的宽度和高度必须大于零。")
    monitors = _native_monitors()
    if _IS_WINDOWS and not monitors:
        raise RuntimeError("无法读取 Windows 显示器信息。")
    if monitors and not any(_is_contained(region, monitor) for monitor in monitors.values()):
        raise ValueError("请将整个扫描框放在同一块显示器内。")
    return region


def _map_rect_to_monitor(
    rect: QRect,
    logical_monitor: QRect,
    physical_monitor: dict[str, int],
    dpr: float,
) -> dict[str, int]:
    """Map offsets from one screen's origin, never scale global coordinates."""
    if rect.isEmpty() or not logical_monitor.contains(rect):
        raise ValueError("请将整个扫描框放在同一块显示器内。")
    x0 = round((rect.x() - logical_monitor.x()) * dpr)
    y0 = round((rect.y() - logical_monitor.y()) * dpr)
    x1 = round((rect.x() + rect.width() - logical_monitor.x()) * dpr)
    y1 = round((rect.y() + rect.height() - logical_monitor.y()) * dpr)
    result = {
        "left": physical_monitor["left"] + x0,
        "top": physical_monitor["top"] + y0,
        "width": x1 - x0,
        "height": y1 - y0,
    }
    if result["width"] <= 0 or result["height"] <= 0 or not _is_contained(result, physical_monitor):
        raise ValueError("扫描区域超出当前显示器。")
    return result


def logical_rect_to_physical(rect: QRect) -> dict[str, int]:
    """Convert a global Qt rectangle; reject a selection spanning screens.

    Prefer ``native_client_region`` for a visible scanner widget: native client
    geometry also accounts for Qt's rounding and actual native window bounds.
    """
    if QGuiApplication.instance() is None:
        raise RuntimeError("请先创建 Qt 应用。")
    screens = [screen for screen in QGuiApplication.screens() if screen.geometry().contains(rect)]
    if rect.isEmpty() or not screens:
        raise ValueError("请将整个扫描框放在同一块显示器内。")
    screen = screens[0]
    geometry = screen.geometry()
    dpr = screen.devicePixelRatio()
    native = _matching_monitor(screen.name(), geometry, dpr, _native_monitors())
    if native is None:
        if _IS_WINDOWS:
            raise RuntimeError("无法将 Qt 显示器映射到 Windows 显示器。")
        native = {
            "left": geometry.x(), "top": geometry.y(),
            "width": round(geometry.width() * dpr),
            "height": round(geometry.height() * dpr),
        }
    return _map_rect_to_monitor(rect, geometry, native, dpr)


def _matching_monitor(name: str, logical: QRect, dpr: float,
                      monitors: dict[str, dict[str, int]]) -> dict[str, int] | None:
    """Qt 6.12 may expose friendly model names instead of DISPLAY device IDs.

    On Windows, Qt retains each native screen origin while scaling its size.
    Require a unique origin-and-size match rather than guessing by list order,
    since identical monitor models and mixed scale factors are common.
    """
    named = monitors.get(name.casefold())
    if named is not None:
        return named
    matching = [monitor for monitor in monitors.values()
                if monitor["left"] == logical.x() and monitor["top"] == logical.y()
                and abs(monitor["width"] - logical.width() * dpr) <= 2
                and abs(monitor["height"] - logical.height() * dpr) <= 2]
    return matching[0] if len(matching) == 1 else None


def native_client_region(widget: Any, inset_logical: int = 0) -> dict[str, int]:
    """Return a visible window's physical client rectangle, optionally inset.

    Call from the GUI thread after the widget is shown. The application's normal
    Qt per-monitor DPI awareness must stay enabled. The final content rectangle
    is required to fit fully inside one monitor.
    """
    if isinstance(inset_logical, bool) or not isinstance(inset_logical, int) or inset_logical < 0:
        raise ValueError("扫描边框宽度必须是非负整数。")
    if not _IS_WINDOWS:
        rect = widget.rect().adjusted(inset_logical, inset_logical, -inset_logical, -inset_logical)
        rect.moveTopLeft(widget.mapToGlobal(rect.topLeft()))
        return logical_rect_to_physical(rect)
    handle = wintypes.HWND(int(widget.winId()))
    rect = wintypes.RECT()
    if not _USER32.GetClientRect(handle, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    origin = wintypes.POINT(rect.left, rect.top)
    if not _USER32.ClientToScreen(handle, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())
    inset = round(inset_logical * widget.devicePixelRatioF())
    return _require_single_monitor({
        "left": origin.x + inset,
        "top": origin.y + inset,
        "width": rect.right - rect.left - 2 * inset,
        "height": rect.bottom - rect.top - 2 * inset,
    })


def set_remote_compatible(enabled: bool) -> None:
    """Choose affinity for all subsequently shown own windows."""
    global _REMOTE_COMPATIBLE
    _REMOTE_COMPATIBLE = bool(enabled)


def is_remote_session() -> bool:
    return bool(_IS_WINDOWS and _USER32.GetSystemMetrics(0x1000))  # SM_REMOTESESSION


def flush_compositor() -> bool:
    """Commit a hidden marker layer before the capture worker reads pixels."""
    if not _IS_WINDOWS:
        return False
    try:
        return ctypes.WinDLL('dwmapi').DwmFlush() == 0
    except (OSError, AttributeError):
        return False


def exclude_from_capture(widget: Any) -> bool:
    """Apply capture policy; remote mode explicitly clears display affinity."""
    if not _IS_WINDOWS:
        return False
    return bool(_USER32.SetWindowDisplayAffinity(int(widget.winId()), 0 if _REMOTE_COMPATIBLE else 0x00000011))


def set_click_through(widget: Any, enabled: bool) -> bool:
    """Toggle native input transparency without replacing the Qt window."""
    if not _IS_WINDOWS:
        return False
    handle = wintypes.HWND(int(widget.winId()))
    ctypes.set_last_error(0)
    style = _GET_WINDOW_LONG(handle, -20)  # GWL_EXSTYLE
    if style == 0 and ctypes.get_last_error():
        return False
    new_style = style | 0x00080000 | 0x00000020 if enabled else style & ~0x00000020
    ctypes.set_last_error(0)
    previous = _SET_WINDOW_LONG(handle, -20, new_style)
    if previous == 0 and ctypes.get_last_error():
        return False
    return bool(_USER32.SetWindowPos(handle, None, 0, 0, 0, 0, 0x0037))


def register_hotkey(hotkey_id: int, modifiers: int, virtual_key: int) -> bool:
    """Register on the GUI thread; receive WM_HOTKEY in its native event filter."""
    if not _IS_WINDOWS:
        return False
    return bool(_USER32.RegisterHotKey(None, hotkey_id, modifiers, virtual_key))


def set_topmost(widget: Any, enabled: bool) -> bool:
    """Change z-order without recreating the HWND or losing capture affinity."""
    if not _IS_WINDOWS:
        from PySide6.QtCore import Qt
        visible = widget.isVisible()
        widget.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:
            widget.show()
        return True
    return bool(_USER32.SetWindowPos(
        int(widget.winId()), wintypes.HWND(-1 if enabled else -2),
        0, 0, 0, 0, 0x0013))


def unregister_hotkey(hotkey_id: int) -> bool:
    if not _IS_WINDOWS:
        return False
    return bool(_USER32.UnregisterHotKey(None, hotkey_id))
