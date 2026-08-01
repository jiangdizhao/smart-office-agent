from __future__ import annotations

import csv
import os
import subprocess
import time
from io import StringIO
from typing import Any

from app.models import ToolResult
from app.presentation_config import presentation_config
from app.tools.presentation_controller import (
    POWERPOINT_PROCESS_NAMES,
    POWERPOINT_WINDOW_KEYWORDS,
    get_presentation_status,
    open_configured_presentation,
    start_configured_slideshow,
)
from app.windows_window_placement import place_window_on_content_monitor


def _powerpoint_pids() -> set[int]:
    if os.name != "nt":
        return set()
    try:
        output = subprocess.check_output(
            ["tasklist", "/fi", "imagename eq POWERPNT.EXE", "/fo", "csv", "/nh"],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return set()
    result: set[int] = set()
    for row in csv.reader(StringIO(output)):
        if len(row) < 2 or row[0].casefold() != "powerpnt.exe":
            continue
        try:
            result.add(int(str(row[1]).replace(",", "").strip()))
        except ValueError:
            continue
    return result


def _placement_result(result: ToolResult, *, slideshow: bool) -> ToolResult:
    status = get_presentation_status()
    pids = {
        int(pid)
        for pid in [result.launched_pid, status.data.get("powerpoint_process_id")]
        if isinstance(pid, int) and pid > 0
    }
    title_keywords = (
        (
            "PowerPoint Slide Show",
            "PowerPoint 幻灯片放映",
            "幻灯片放映",
        )
        if slideshow
        else (
            presentation_config.presentation_path.name,
            "PowerPoint",
        )
    )
    placement = place_window_on_content_monitor(
        process_names=POWERPOINT_PROCESS_NAMES,
        pids=pids,
        title_keywords=title_keywords,
        timeout_seconds=10.0 if slideshow else 8.0,
    )
    placement_ok = bool(placement.get("placement_verified"))
    verified = bool(result.ok and placement_ok)
    requested_state = dict(result.data.get("requested_state") or {})
    requested_state["content_monitor_device"] = (
        placement.get("target_monitor") or {}
    ).get("device")
    return result.model_copy(
        update={
            "ok": verified,
            "message": (
                "PowerPoint slide show opened, moved to the content display, maximized, and verified."
                if slideshow and placement_ok
                else "PowerPoint opened, moved to the content display, maximized, and verified."
                if placement_ok
                else (
                    "PowerPoint started, but its visible maximized window was not verified "
                    "on the content display."
                )
            ),
            "data": {
                **result.data,
                "requested_state": requested_state,
                "window_placement": placement,
                "window_placement_verified": placement_ok,
                "content_monitor_device": (
                    placement.get("target_monitor") or {}
                ).get("device"),
                "verified": verified,
            },
            "raw": {
                **result.raw,
                "window_placement": placement,
            },
        }
    )


def open_configured_presentation_on_content_display() -> ToolResult:
    result = open_configured_presentation()
    if not result.ok:
        return result
    return _placement_result(result, slideshow=False)


def start_configured_slideshow_on_content_display() -> ToolResult:
    result = start_configured_slideshow()
    if not result.ok:
        return result
    return _placement_result(result, slideshow=True)


def _wait_for_powerpoint_exit(timeout_seconds: float) -> set[int]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        pids = _powerpoint_pids()
        if not pids or time.monotonic() >= deadline:
            return pids
        time.sleep(0.2)


def _force_kill_powerpoint() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["taskkill", "/IM", "POWERPNT.EXE", "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "attempted": True,
            "return_code": completed.returncode,
            "stdout": completed.stdout[-1000:],
            "stderr": completed.stderr[-1000:],
        }
    except Exception as exc:
        return {
            "attempted": True,
            "error": f"{type(exc).__name__}: {exc}",
        }


def close_powerpoint_discarding_changes() -> ToolResult:
    """Close every PowerPoint window without saving and verify process exit.

    This is an explicit exhibition command. Marking Presentation.Saved=True tells
    PowerPoint to discard pending edits rather than displaying a Save prompt.
    """

    tool_name = "presentation_close"
    started_at = time.monotonic()
    observed_before = get_presentation_status().data
    com_details: dict[str, Any] = {
        "connected": False,
        "slide_show_windows_closed": 0,
        "presentations_closed": 0,
        "changes_discarded": True,
        "application_quit_requested": False,
    }

    if os.name != "nt":
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message="PowerPoint close is available only on Windows.",
            expected_process_names=POWERPOINT_PROCESS_NAMES,
            expected_window_keywords=POWERPOINT_WINDOW_KEYWORDS,
            data={
                "execution_mode": "unsupported",
                "requested_state": {"presentation_open": False},
                "observed_before": observed_before,
                "verified": False,
            },
        )

    com_error: str | None = None
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            try:
                application = win32com.client.GetActiveObject("PowerPoint.Application")
            except Exception:
                application = None
            if application is not None:
                com_details["connected"] = True
                try:
                    windows = application.SlideShowWindows
                    for index in range(int(windows.Count), 0, -1):
                        try:
                            windows.Item(index).View.Exit()
                            com_details["slide_show_windows_closed"] += 1
                        except Exception:
                            continue
                except Exception:
                    pass

                try:
                    presentations = application.Presentations
                    for index in range(int(presentations.Count), 0, -1):
                        item = presentations.Item(index)
                        try:
                            item.Saved = True
                        except Exception:
                            pass
                        try:
                            item.Close()
                            com_details["presentations_closed"] += 1
                        except Exception:
                            continue
                except Exception:
                    pass

                try:
                    application.Quit()
                    com_details["application_quit_requested"] = True
                except Exception as exc:
                    com_error = f"{type(exc).__name__}: {exc}"
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        com_error = f"{type(exc).__name__}: {exc}"

    remaining = _wait_for_powerpoint_exit(4.0)
    force_details: dict[str, Any] = {"attempted": False}
    if remaining:
        force_details = _force_kill_powerpoint()
        remaining = _wait_for_powerpoint_exit(5.0)

    verified = not remaining
    return ToolResult(
        tool_name=tool_name,
        ok=verified,
        message=(
            "PowerPoint was closed without saving changes and process exit was verified."
            if verified
            else "PowerPoint could not be fully closed."
        ),
        expected_process_names=POWERPOINT_PROCESS_NAMES,
        expected_window_keywords=POWERPOINT_WINDOW_KEYWORDS,
        data={
            "execution_mode": "real",
            "requested_state": {
                "presentation_open": False,
                "slideshow_active": False,
                "powerpoint_process_running": False,
            },
            "observed_before": observed_before,
            "discard_unsaved_changes": True,
            "com_close": com_details,
            "com_error": com_error,
            "force_close": force_details,
            "remaining_powerpoint_pids": sorted(remaining),
            "verified": verified,
            "duration_ms": round((time.monotonic() - started_at) * 1000),
        },
        raw={
            "com_close": com_details,
            "com_error": com_error,
            "force_close": force_details,
            "remaining_powerpoint_pids": sorted(remaining),
        },
    )
