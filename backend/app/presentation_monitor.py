from __future__ import annotations

from typing import Any

from app.presentation_config import presentation_config


def _target_monitor() -> dict[str, Any] | None:
    try:
        import win32api
    except ImportError:
        return None

    target = presentation_config.target_monitor_device.casefold()
    for monitor, _dc, _rect in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(monitor)
        device = str(info.get("Device", ""))
        if device.casefold() != target:
            continue
        left, top, right, bottom = tuple(info["Monitor"])
        return {
            "device": device,
            "left": int(left),
            "top": int(top),
            "right": int(right),
            "bottom": int(bottom),
            "width": int(right - left),
            "height": int(bottom - top),
            "primary": bool(info.get("Flags", 0) & 1),
        }
    return None


def _active_slideshow_hwnd() -> int | None:
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None

    pythoncom.CoInitialize()
    try:
        try:
            application = win32com.client.GetActiveObject("PowerPoint.Application")
        except Exception:
            return None
        try:
            windows = application.SlideShowWindows
            if int(windows.Count) < 1:
                return None
            return int(windows.Item(1).HWND)
        except Exception:
            return None
    finally:
        pythoncom.CoUninitialize()


def _monitor_for_point(x: int, y: int) -> dict[str, Any] | None:
    try:
        import win32api
    except ImportError:
        return None

    for monitor, _dc, _rect in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(monitor)
        left, top, right, bottom = tuple(info["Monitor"])
        if left <= x < right and top <= y < bottom:
            return {
                "device": str(info.get("Device", "")),
                "left": int(left),
                "top": int(top),
                "right": int(right),
                "bottom": int(bottom),
                "width": int(right - left),
                "height": int(bottom - top),
                "primary": bool(info.get("Flags", 0) & 1),
            }
    return None


def inspect_slideshow_monitor() -> dict[str, Any]:
    result: dict[str, Any] = {
        "target_monitor_device": presentation_config.target_monitor_device,
        "target_monitor_number": presentation_config.target_monitor_number,
        "target_monitor_available": False,
        "slideshow_window_hwnd": None,
        "slideshow_window_rect": None,
        "slideshow_monitor_device": None,
        "monitor_placement_enforced": False,
    }
    target = _target_monitor()
    result["target_monitor_available"] = target is not None
    if target is not None:
        result["target_monitor_bounds"] = target

    hwnd = _active_slideshow_hwnd()
    result["slideshow_window_hwnd"] = hwnd
    if hwnd is None:
        return result

    try:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception as exc:
        result["monitor_error"] = str(exc)
        return result

    rect = {
        "left": int(left),
        "top": int(top),
        "right": int(right),
        "bottom": int(bottom),
        "width": int(right - left),
        "height": int(bottom - top),
    }
    result["slideshow_window_rect"] = rect
    observed = _monitor_for_point((left + right) // 2, (top + bottom) // 2)
    if observed is not None:
        result["slideshow_monitor_device"] = observed["device"]
        result["slideshow_monitor_bounds"] = observed
        result["monitor_placement_enforced"] = (
            observed["device"].casefold()
            == presentation_config.target_monitor_device.casefold()
        )
    return result


def place_slideshow_on_target_monitor() -> dict[str, Any]:
    target = _target_monitor()
    if target is None:
        result = inspect_slideshow_monitor()
        result.update(
            {
                "placement_attempted": False,
                "placement_ok": False,
                "monitor_error": (
                    f"Target monitor was not found: {presentation_config.target_monitor_device}"
                ),
            }
        )
        return result

    hwnd = _active_slideshow_hwnd()
    if hwnd is None:
        result = inspect_slideshow_monitor()
        result.update(
            {
                "placement_attempted": False,
                "placement_ok": False,
                "monitor_error": "PowerPoint slide-show window was not found.",
            }
        )
        return result

    try:
        import win32con
        import win32gui

        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOP,
            target["left"],
            target["top"],
            target["width"],
            target["height"],
            win32con.SWP_SHOWWINDOW | win32con.SWP_FRAMECHANGED,
        )
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    except Exception as exc:
        result = inspect_slideshow_monitor()
        result.update(
            {
                "placement_attempted": True,
                "placement_ok": False,
                "monitor_error": str(exc),
            }
        )
        return result

    result = inspect_slideshow_monitor()
    result.update(
        {
            "placement_attempted": True,
            "placement_ok": bool(result.get("monitor_placement_enforced")),
        }
    )
    return result
