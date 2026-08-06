from __future__ import annotations

import csv
import os
import subprocess
import time
from io import StringIO
from pathlib import Path
from typing import Any, Iterable

CONTENT_MONITOR_ENV = "SMART_OFFICE_CONTENT_MONITOR_DEVICE"
WINDOW_PLACEMENT_TIMEOUT_ENV = "SMART_OFFICE_WINDOW_PLACEMENT_TIMEOUT_SECONDS"
DEFAULT_CONTENT_MONITOR_DEVICE = r"\\.\DISPLAY2"


def _normalise_process_name(value: str) -> str:
    return Path(str(value).strip().strip('"')).name.casefold()


def _tasklist_processes() -> dict[int, str]:
    if os.name != "nt":
        return {}
    try:
        output = subprocess.check_output(
            ["tasklist", "/fo", "csv", "/nh"],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return {}

    result: dict[int, str] = {}
    for row in csv.reader(StringIO(output)):
        if len(row) < 2:
            continue
        try:
            pid = int(str(row[1]).replace(",", "").strip())
        except ValueError:
            continue
        result[pid] = str(row[0]).strip()
    return result


def enumerate_monitors() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        import win32api
    except ImportError:
        return []

    monitors: list[dict[str, Any]] = []
    for handle, _dc, _rect in win32api.EnumDisplayMonitors():
        try:
            info = win32api.GetMonitorInfo(handle)
            left, top, right, bottom = tuple(info["Monitor"])
            work_left, work_top, work_right, work_bottom = tuple(info["Work"])
            monitors.append(
                {
                    "handle": int(handle),
                    "device": str(info.get("Device", "")),
                    "left": int(left),
                    "top": int(top),
                    "right": int(right),
                    "bottom": int(bottom),
                    "width": int(right - left),
                    "height": int(bottom - top),
                    "work_left": int(work_left),
                    "work_top": int(work_top),
                    "work_right": int(work_right),
                    "work_bottom": int(work_bottom),
                    "work_width": int(work_right - work_left),
                    "work_height": int(work_bottom - work_top),
                    "primary": bool(info.get("Flags", 0) & 1),
                }
            )
        except Exception:
            continue
    return monitors


def content_monitor() -> dict[str, Any] | None:
    monitors = enumerate_monitors()
    if not monitors:
        return None

    configured = os.getenv(CONTENT_MONITOR_ENV, "").strip()
    requested = configured or DEFAULT_CONTENT_MONITOR_DEVICE
    for monitor in monitors:
        if str(monitor["device"]).casefold() == requested.casefold():
            return monitor

    # Fallback only when DISPLAY2 is genuinely unavailable. The exhibition machine
    # is arranged 3 / 1 / 2 from left to right, so DISPLAY2 is the normal target.
    return max(
        monitors,
        key=lambda item: (
            int(item["right"]),
            int(item["left"]),
            int(item["width"] * item["height"]),
        ),
    )


def enumerate_top_level_windows() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        import win32gui
        import win32process
    except ImportError:
        return []

    processes = _tasklist_processes()
    windows: list[dict[str, Any]] = []

    def collect(hwnd: int, _extra: object) -> None:
        try:
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            class_name = str(win32gui.GetClassName(hwnd) or "").strip()
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = max(0, int(right - left))
            height = max(0, int(bottom - top))
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "pid": int(pid),
                    "process_name": processes.get(int(pid), ""),
                    "title": title,
                    "class_name": class_name,
                    "visible": bool(win32gui.IsWindowVisible(hwnd)),
                    "iconic": bool(win32gui.IsIconic(hwnd)),
                    "zoomed": bool(win32gui.IsZoomed(hwnd)),
                    "left": int(left),
                    "top": int(top),
                    "right": int(right),
                    "bottom": int(bottom),
                    "width": width,
                    "height": height,
                    "area": width * height,
                }
            )
        except Exception:
            return

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return []
    return windows


def _window_matches(
    window: dict[str, Any],
    *,
    process_names: set[str],
    pids: set[int],
    title_keywords: tuple[str, ...],
) -> bool:
    title = str(window.get("title") or "")
    process_name = _normalise_process_name(str(window.get("process_name") or ""))
    pid = int(window.get("pid") or 0)
    title_match = any(keyword in title.casefold() for keyword in title_keywords)
    process_match = bool(process_names and process_name in process_names)
    pid_match = bool(pids and pid in pids)
    return title_match or process_match or pid_match


def matching_windows(
    *,
    process_names: Iterable[str] = (),
    pids: Iterable[int] = (),
    title_keywords: Iterable[str] = (),
) -> list[dict[str, Any]]:
    expected_process_names = {
        _normalise_process_name(name) for name in process_names if str(name).strip()
    }
    expected_pids = {int(pid) for pid in pids if int(pid) > 0}
    expected_keywords = tuple(
        str(keyword).strip().casefold()
        for keyword in title_keywords
        if str(keyword).strip()
    )

    candidates: list[dict[str, Any]] = []
    for window in enumerate_top_level_windows():
        if not _window_matches(
            window,
            process_names=expected_process_names,
            pids=expected_pids,
            title_keywords=expected_keywords,
        ):
            continue
        title = str(window.get("title") or "")
        class_name = str(window.get("class_name") or "").casefold()
        if class_name in {"shell_traywnd", "progman", "workerw"}:
            continue
        score = min(int(window.get("area") or 0), 40_000_000)
        if title:
            score += 20_000_000
        if window.get("visible"):
            score += 10_000_000
        if window.get("iconic"):
            score += 5_000_000
        if expected_keywords and any(keyword in title.casefold() for keyword in expected_keywords):
            score += 80_000_000
        if expected_pids and int(window.get("pid") or 0) in expected_pids:
            score += 60_000_000
        if expected_process_names and _normalise_process_name(
            str(window.get("process_name") or "")
        ) in expected_process_names:
            score += 50_000_000
        candidates.append({**window, "score": score})

    candidates.sort(key=lambda item: int(item["score"]), reverse=True)
    return candidates


def inspect_window_placement(hwnd: int, target: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "hwnd": int(hwnd),
        "target_monitor": target,
        "window_visible": False,
        "window_minimized": None,
        "window_maximized": None,
        "window_rect": None,
        "observed_monitor_device": None,
        "on_target_monitor": False,
        "maximization_required": False,
        "placement_policy": "move_only",
        "placement_verified": False,
    }
    if os.name != "nt":
        result["error"] = "Window placement is available only on Windows."
        return result
    try:
        import win32api
        import win32gui
    except ImportError:
        result["error"] = "pywin32 window APIs are unavailable."
        return result

    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        result["window_visible"] = bool(win32gui.IsWindowVisible(hwnd))
        result["window_minimized"] = bool(win32gui.IsIconic(hwnd))
        result["window_maximized"] = bool(win32gui.IsZoomed(hwnd))
        result["window_rect"] = {
            "left": int(left),
            "top": int(top),
            "right": int(right),
            "bottom": int(bottom),
            "width": int(right - left),
            "height": int(bottom - top),
        }
        monitor_handle = win32api.MonitorFromWindow(hwnd, 2)
        monitor_info = win32api.GetMonitorInfo(monitor_handle)
        observed_device = str(monitor_info.get("Device", ""))
        result["observed_monitor_device"] = observed_device
        target_device = str((target or {}).get("device") or "")
        result["on_target_monitor"] = bool(
            target_device and observed_device.casefold() == target_device.casefold()
        )
        # Maximized state is intentionally informational only. Some Office/UWP
        # windows report it unreliably, which previously converted a successful
        # launch into a false failure.
        result["placement_verified"] = bool(
            result["window_visible"]
            and not result["window_minimized"]
            and result["on_target_monitor"]
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _bring_to_foreground(hwnd: int) -> None:
    try:
        import win32api
        import win32gui
        import win32process

        foreground = win32gui.GetForegroundWindow()
        current_thread = win32api.GetCurrentThreadId()
        foreground_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        attached_foreground = False
        attached_target = False
        try:
            if foreground_thread and foreground_thread != current_thread:
                win32process.AttachThreadInput(current_thread, foreground_thread, True)
                attached_foreground = True
            if target_thread and target_thread != current_thread:
                win32process.AttachThreadInput(current_thread, target_thread, True)
                attached_target = True
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetActiveWindow(hwnd)
        finally:
            if attached_target:
                win32process.AttachThreadInput(current_thread, target_thread, False)
            if attached_foreground:
                win32process.AttachThreadInput(current_thread, foreground_thread, False)
    except Exception:
        try:
            import win32con
            import win32gui

            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOP,
                0,
                0,
                0,
                0,
                0x0002 | 0x0001 | win32con.SWP_SHOWWINDOW,
            )
        except Exception:
            pass


def _normal_window_geometry(hwnd: int, target: dict[str, Any]) -> tuple[int, int, int, int]:
    try:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = max(320, int(right - left))
        height = max(240, int(bottom - top))
    except Exception:
        width = 1200
        height = 800

    work_width = max(320, int(target["work_width"]))
    work_height = max(240, int(target["work_height"]))
    margin = 16 if work_width >= 640 and work_height >= 480 else 0
    width = min(width, max(320, work_width - margin * 2))
    height = min(height, max(240, work_height - margin * 2))
    x = int(target["work_left"]) + max(margin, (work_width - width) // 2)
    y = int(target["work_top"]) + max(margin, (work_height - height) // 2)
    return x, y, width, height


def place_window_on_content_monitor(
    *,
    process_names: Iterable[str] = (),
    pids: Iterable[int] = (),
    title_keywords: Iterable[str] = (),
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    target = content_monitor()
    result: dict[str, Any] = {
        "target_monitor": target,
        "requested_monitor_device": DEFAULT_CONTENT_MONITOR_DEVICE,
        "window_found": False,
        "placement_attempted": False,
        "placement_verified": False,
        "maximization_required": False,
        "placement_policy": "move_only",
        "candidate_windows": [],
        "selected_window": None,
        "attempts": 0,
    }
    if os.name != "nt":
        result["error"] = "Window placement is available only on Windows."
        return result
    if target is None:
        result["error"] = "No Windows monitor was detected."
        return result

    if timeout_seconds is None:
        try:
            timeout_seconds = float(os.getenv(WINDOW_PLACEMENT_TIMEOUT_ENV, "10"))
        except ValueError:
            timeout_seconds = 10.0
    deadline = time.monotonic() + max(0.5, timeout_seconds)

    try:
        import win32con
        import win32gui
    except ImportError:
        result["error"] = "pywin32 window APIs are unavailable."
        return result

    while time.monotonic() <= deadline:
        result["attempts"] = int(result["attempts"]) + 1
        candidates = matching_windows(
            process_names=process_names,
            pids=pids,
            title_keywords=title_keywords,
        )
        result["candidate_windows"] = candidates[:5]
        if not candidates:
            time.sleep(0.2)
            continue

        selected = candidates[0]
        hwnd = int(selected["hwnd"])
        result["window_found"] = True
        result["selected_window"] = selected
        result["placement_attempted"] = True
        try:
            # Restore and move only. Window size/maximization is left to the user or
            # the application itself; it is no longer a success criterion.
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.05)
            x, y, width, height = _normal_window_geometry(hwnd, target)
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOP,
                x,
                y,
                width,
                height,
                win32con.SWP_SHOWWINDOW | win32con.SWP_FRAMECHANGED,
            )
            _bring_to_foreground(hwnd)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            time.sleep(0.2)
            continue

        time.sleep(0.2)
        inspection = inspect_window_placement(hwnd, target)
        result.update(inspection)
        if inspection.get("placement_verified"):
            return result

    if "error" not in result:
        result["error"] = (
            "A matching application window was not confirmed on DISPLAY2 before timeout. "
            "This diagnostic does not invalidate a successful application launch."
        )
    return result
