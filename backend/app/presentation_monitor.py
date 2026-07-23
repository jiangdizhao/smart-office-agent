from __future__ import annotations

import time
from typing import Any

from app.presentation_config import presentation_config

_SLIDESHOW_WINDOW_TIMEOUT_SECONDS = 6.0
_PLACEMENT_VERIFY_TIMEOUT_SECONDS = 4.0
_MONITOR_POLL_INTERVAL_SECONDS = 0.15


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


def _enumerate_powerpoint_slideshow_hwnd(
    *,
    process_id: int | None,
    main_hwnd: int | None,
    presentation_name: str,
) -> int | None:
    """Find a visible PowerPoint slide-show window.

    Newer Office builds normally expose SlideShowWindow.HWND. Some late-bound
    COM builds do not expose either SlideShowWindow.HWND or Application.HWND, so
    the final fallback accepts only windows with strong slide-show class/title
    markers instead of depending on an application process id.
    """

    try:
        import win32gui
        import win32process
    except ImportError:
        return None

    candidates: list[tuple[int, int]] = []
    expected_title = presentation_name.casefold()

    def collect(hwnd: int, _extra: Any) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            _thread_id, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
            if process_id is not None and int(owner_pid) != process_id:
                return
            if main_hwnd and hwnd == main_hwnd:
                return

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            area = max(0, right - left) * max(0, bottom - top)
            if area <= 0:
                return

            class_name = win32gui.GetClassName(hwnd).casefold()
            title = win32gui.GetWindowText(hwnd).casefold()
            class_marker = "screenclass" in class_name or "slideshow" in class_name
            title_marker = (
                "powerpoint slide show" in title
                or "powerpoint 幻灯片放映" in title
                or "幻灯片放映" in title
            )

            # Without a trusted process id, do not accept an ordinary PowerPoint
            # editing window merely because its title contains the presentation name.
            if process_id is None and not (class_marker or title_marker):
                return

            score = min(area, 20_000_000)
            if class_marker:
                score += 100_000_000
            if title_marker:
                score += 80_000_000
            if expected_title and expected_title in title:
                score += 40_000_000
            candidates.append((score, hwnd))
        except Exception:
            return

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return int(candidates[0][1])


def _active_slideshow_hwnd() -> int | None:
    try:
        import pythoncom
        import win32com.client
        import win32process
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
            slide_show_window = windows.Item(1)
            presentation_name = str(slide_show_window.Presentation.Name)
        except Exception:
            return None

        try:
            hwnd = int(slide_show_window.HWND)
            if hwnd:
                return hwnd
        except Exception:
            pass

        main_hwnd: int | None = None
        process_id: int | None = None
        try:
            main_hwnd = int(application.HWND)
            _thread_id, owner_pid = win32process.GetWindowThreadProcessId(main_hwnd)
            process_id = int(owner_pid)
        except Exception:
            pass

        hwnd = _enumerate_powerpoint_slideshow_hwnd(
            process_id=process_id,
            main_hwnd=main_hwnd,
            presentation_name=presentation_name,
        )
        if hwnd is not None:
            return hwnd

        # Application.HWND is absent on some Office late-bound COM builds. Repeat
        # with strict slide-show class/title markers and no process-id dependency.
        if process_id is not None:
            return _enumerate_powerpoint_slideshow_hwnd(
                process_id=None,
                main_hwnd=main_hwnd,
                presentation_name=presentation_name,
            )
        return None
    finally:
        pythoncom.CoUninitialize()


def _wait_for_slideshow_hwnd(timeout_seconds: float) -> tuple[int | None, int]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    attempts = 0
    while True:
        attempts += 1
        hwnd = _active_slideshow_hwnd()
        if hwnd is not None:
            return hwnd, attempts
        if time.monotonic() >= deadline:
            return None, attempts
        time.sleep(_MONITOR_POLL_INTERVAL_SECONDS)


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


def inspect_slideshow_monitor(*, hwnd: int | None = None) -> dict[str, Any]:
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

    resolved_hwnd = hwnd if hwnd is not None else _active_slideshow_hwnd()
    result["slideshow_window_hwnd"] = resolved_hwnd
    if resolved_hwnd is None:
        return result

    try:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(resolved_hwnd)
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


def place_slideshow_on_target_monitor(
    *,
    window_timeout_seconds: float = _SLIDESHOW_WINDOW_TIMEOUT_SECONDS,
    verification_timeout_seconds: float = _PLACEMENT_VERIFY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Wait for the asynchronous PowerPoint slide-show window, place it, and verify.

    SlideShowSettings.Run may return before Windows exposes the top-level slide-show
    HWND. The old implementation inspected once and converted this transient state
    into a permanent Gate 2B failure. This implementation waits for the window and
    verifies a freshly observed final monitor state.
    """

    started_at = time.monotonic()
    target = _target_monitor()
    if target is None:
        result = inspect_slideshow_monitor()
        result.update(
            {
                "placement_attempted": False,
                "placement_ok": False,
                "placement_attempts": 0,
                "monitor_error": (
                    f"Target monitor was not found: {presentation_config.target_monitor_device}"
                ),
            }
        )
        return result

    hwnd, discovery_attempts = _wait_for_slideshow_hwnd(window_timeout_seconds)
    if hwnd is None:
        result = inspect_slideshow_monitor()
        result.update(
            {
                "placement_attempted": False,
                "placement_ok": False,
                "placement_attempts": discovery_attempts,
                "placement_wait_ms": round((time.monotonic() - started_at) * 1000),
                "monitor_error": "PowerPoint slide-show window was not found before timeout.",
            }
        )
        return result

    try:
        import win32con
        import win32gui
    except ImportError:
        result = inspect_slideshow_monitor(hwnd=hwnd)
        result.update(
            {
                "placement_attempted": False,
                "placement_ok": False,
                "placement_attempts": discovery_attempts,
                "monitor_error": "pywin32 window APIs are unavailable.",
            }
        )
        return result

    placement_attempts = 0
    deadline = time.monotonic() + max(0.0, verification_timeout_seconds)
    last_result: dict[str, Any] = inspect_slideshow_monitor(hwnd=hwnd)

    while True:
        placement_attempts += 1
        try:
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
            last_result = inspect_slideshow_monitor(hwnd=hwnd)
            last_result.update(
                {
                    "placement_attempted": True,
                    "placement_ok": False,
                    "placement_attempts": placement_attempts,
                    "placement_wait_ms": round((time.monotonic() - started_at) * 1000),
                    "monitor_error": str(exc),
                }
            )
            return last_result

        time.sleep(_MONITOR_POLL_INTERVAL_SECONDS)
        last_result = inspect_slideshow_monitor(hwnd=hwnd)
        if bool(last_result.get("monitor_placement_enforced")):
            break
        if time.monotonic() >= deadline:
            break

    last_result.update(
        {
            "placement_attempted": True,
            "placement_ok": bool(last_result.get("monitor_placement_enforced")),
            "placement_attempts": placement_attempts,
            "slideshow_discovery_attempts": discovery_attempts,
            "placement_wait_ms": round((time.monotonic() - started_at) * 1000),
        }
    )
    if not last_result["placement_ok"] and "monitor_error" not in last_result:
        last_result["monitor_error"] = (
            "PowerPoint slide-show window was found but final placement on the configured monitor was not verified."
        )
    return last_result
