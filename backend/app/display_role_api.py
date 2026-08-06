from __future__ import annotations

import os
import threading
import time
from typing import Any, Literal

from fastapi import APIRouter

from app.presentation_config import presentation_config

router = APIRouter(tags=["display-layout"])
DisplayRole = Literal["leftmost", "middle", "rightmost"]

_SERVICE_LOCK = threading.RLock()
_WATCHER_STARTED = False
_LAST_OUTLOOK_MOVE: dict[str, Any] = {
    "enabled": False,
    "target_device": None,
    "moved_window_count": 0,
    "last_error": None,
    "last_run_at": None,
}


def _truthy(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().casefold() in {"1", "true", "yes", "on"}


def _enumerate_displays() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        import win32api
    except ImportError:
        return []

    displays: list[dict[str, Any]] = []
    for handle, _dc, _rect in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(handle)
        left, top, right, bottom = tuple(info["Monitor"])
        work_left, work_top, work_right, work_bottom = tuple(info["Work"])
        displays.append(
            {
                "device": str(info.get("Device", "")),
                "left": int(left),
                "top": int(top),
                "right": int(right),
                "bottom": int(bottom),
                "width": int(right - left),
                "height": int(bottom - top),
                "work_left": int(work_left),
                "work_top": int(work_top),
                "work_width": int(work_right - work_left),
                "work_height": int(work_bottom - work_top),
                "primary": bool(info.get("Flags", 0) & 1),
            }
        )
    displays.sort(key=lambda item: (item["left"], item["top"]))
    for index, display in enumerate(displays):
        display["spatial_index"] = index
        display["role"] = (
            "leftmost"
            if index == 0
            else "rightmost"
            if index == len(displays) - 1
            else "middle"
        )
    return displays


def _display_for_role(role: DisplayRole) -> dict[str, Any] | None:
    displays = _enumerate_displays()
    if not displays:
        return None
    if role == "leftmost":
        return displays[0]
    if role == "rightmost":
        return displays[-1]
    return displays[len(displays) // 2]


def _configured_office_role() -> DisplayRole:
    configured = os.getenv("SMART_OFFICE_PRESENTATION_MONITOR_POSITION", "rightmost").strip().casefold()
    return configured if configured in {"leftmost", "middle", "rightmost"} else "rightmost"  # type: ignore[return-value]


def _configure_presentation_target() -> dict[str, Any] | None:
    target = _display_for_role(_configured_office_role())
    if target is None:
        return None
    object.__setattr__(presentation_config, "target_monitor_device", target["device"])
    object.__setattr__(presentation_config, "target_monitor_number", int(target["spatial_index"]) + 1)
    return target


def _is_outlook_window(hwnd: int) -> bool:
    try:
        import win32gui

        if not win32gui.IsWindowVisible(hwnd):
            return False
        class_name = win32gui.GetClassName(hwnd).casefold()
        title = win32gui.GetWindowText(hwnd).casefold()
        return class_name.startswith("rctrl_renwnd32") or " - outlook" in title or title.endswith("outlook")
    except Exception:
        return False


def _move_outlook_windows_once(target: dict[str, Any]) -> int:
    try:
        import win32con
        import win32gui
    except ImportError:
        return 0

    candidates: list[int] = []
    win32gui.EnumWindows(
        lambda hwnd, _extra: candidates.append(hwnd) if _is_outlook_window(hwnd) else None,
        None,
    )
    moved = 0
    for hwnd in candidates:
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2
            if target["left"] <= center_x < target["right"] and target["top"] <= center_y < target["bottom"]:
                continue
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOP,
                target["work_left"],
                target["work_top"],
                target["work_width"],
                target["work_height"],
                win32con.SWP_SHOWWINDOW | win32con.SWP_FRAMECHANGED,
            )
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            moved += 1
        except Exception:
            continue
    return moved


def _outlook_window_watcher() -> None:
    interval = max(0.5, float(os.getenv("SMART_OFFICE_OUTLOOK_MONITOR_POLL_SECONDS", "1.0")))
    while True:
        target = _display_for_role(_configured_office_role())
        with _SERVICE_LOCK:
            _LAST_OUTLOOK_MOVE["enabled"] = True
            _LAST_OUTLOOK_MOVE["target_device"] = target["device"] if target else None
            _LAST_OUTLOOK_MOVE["last_run_at"] = time.time()
        if target is not None:
            try:
                moved = _move_outlook_windows_once(target)
                with _SERVICE_LOCK:
                    _LAST_OUTLOOK_MOVE["moved_window_count"] = moved
                    _LAST_OUTLOOK_MOVE["last_error"] = None
            except Exception as exc:
                with _SERVICE_LOCK:
                    _LAST_OUTLOOK_MOVE["last_error"] = str(exc)
        time.sleep(interval)


def start_display_role_service() -> None:
    global _WATCHER_STARTED
    if os.name != "nt":
        return
    _configure_presentation_target()
    if not _truthy("SMART_OFFICE_MOVE_OUTLOOK_TO_PRESENTATION_MONITOR", "true"):
        return
    with _SERVICE_LOCK:
        if _WATCHER_STARTED:
            return
        _WATCHER_STARTED = True
    threading.Thread(
        target=_outlook_window_watcher,
        name="smart-office-outlook-display-role",
        daemon=True,
    ).start()


@router.get("/api/display-layout")
def display_layout() -> dict[str, Any]:
    displays = _enumerate_displays()
    target = _display_for_role(_configured_office_role())
    with _SERVICE_LOCK:
        outlook_status = dict(_LAST_OUTLOOK_MOVE)
    return {
        "ok": True,
        "platform": os.name,
        "display_count": len(displays),
        "displays": displays,
        "interaction_role": "leftmost",
        "agent_role": "middle",
        "office_role": _configured_office_role(),
        "office_target_device": target["device"] if target else presentation_config.target_monitor_device,
        "presentation_target_device": presentation_config.target_monitor_device,
        "presentation_target_number": presentation_config.target_monitor_number,
        "outlook_window_watcher": outlook_status,
    }
