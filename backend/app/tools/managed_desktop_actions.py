from __future__ import annotations

import os
import subprocess
from typing import Any

from app.models import ToolResult
from app.tools.managed_application_controller import (
    close_managed_application,
    open_managed_application,
    play_random_music,
    stop_music,
)
from app.windows_window_placement import place_window_on_content_monitor

_APPLICATION_SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    "teams": {
        "process_names": ("ms-teams.exe", "teams.exe", "ApplicationFrameHost.exe"),
        "title_keywords": ("Microsoft Teams", "Teams"),
    },
    "onenote": {
        "process_names": ("onenote.exe", "ApplicationFrameHost.exe"),
        "title_keywords": ("OneNote",),
    },
}

_MEDIA_PROCESS_NAMES = (
    "Microsoft.Media.Player.exe",
    "MediaPlayer.exe",
    "Music.UI.exe",
    "wmplayer.exe",
    "vlc.exe",
    "ApplicationFrameHost.exe",
)


def _pid_values(result: ToolResult) -> set[int]:
    values: set[int] = set()
    if result.launched_pid:
        values.add(int(result.launched_pid))
    for key in ("processes", "detected_processes"):
        payload = result.data.get(key)
        if isinstance(payload, dict):
            for pid in payload:
                try:
                    values.add(int(pid))
                except (TypeError, ValueError):
                    continue
    for pid in result.data.get("candidate_pids", []) or []:
        try:
            values.add(int(pid))
        except (TypeError, ValueError):
            continue
    return values


def _reactivate_application(application: str) -> dict[str, Any]:
    protocol = "msteams:" if application == "teams" else "onenote:"
    try:
        completed = subprocess.run(
            ["cmd", "/c", "start", "", protocol],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "attempted": True,
            "protocol": protocol,
            "return_code": completed.returncode,
            "stdout": completed.stdout[-500:],
            "stderr": completed.stderr[-500:],
        }
    except Exception as exc:
        return {
            "attempted": True,
            "protocol": protocol,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _merge_placement(
    result: ToolResult,
    placement: dict[str, Any],
    *,
    subject: str,
) -> ToolResult:
    placement_ok = bool(placement.get("placement_verified"))
    original_verified = result.data.get("verified") is True
    ok = bool(result.ok and original_verified and placement_ok)
    message = (
        f"{subject} opened, moved to the content display, maximized, and verified."
        if ok
        else (
            f"{subject} was started, but its visible maximized window was not verified "
            "on the content display."
        )
    )
    return result.model_copy(
        update={
            "ok": ok,
            "message": message,
            "data": {
                **result.data,
                "launch_verified": original_verified,
                "window_placement": placement,
                "window_placement_verified": placement_ok,
                "verified": ok,
                "content_monitor_device": (placement.get("target_monitor") or {}).get(
                    "device"
                ),
                "status_scope": "managed_application_and_window_placement",
            },
            "raw": {
                **result.raw,
                "window_placement": placement,
            },
        }
    )


def open_managed_application_on_content_display(application: str) -> ToolResult:
    application = application.strip().casefold()
    result = open_managed_application(application)
    if not result.ok:
        return result

    spec = _APPLICATION_SPECS.get(application)
    if spec is None:
        return result.model_copy(
            update={
                "ok": False,
                "message": f"No content-display placement specification exists for {application}.",
                "data": {**result.data, "verified": False},
            }
        )

    placement = place_window_on_content_monitor(
        process_names=spec["process_names"],
        pids=_pid_values(result),
        title_keywords=spec["title_keywords"],
        timeout_seconds=6.0,
    )
    reactivation: dict[str, Any] | None = None
    if not placement.get("placement_verified"):
        # Teams and OneNote often keep a tray/background process while their main
        # window is closed. Reissuing the registered URI requests a real foreground
        # window, after which placement is retried.
        reactivation = _reactivate_application(application)
        placement = place_window_on_content_monitor(
            process_names=spec["process_names"],
            pids=_pid_values(result),
            title_keywords=spec["title_keywords"],
            timeout_seconds=8.0,
        )
        placement["reactivation"] = reactivation

    label = "Microsoft Teams" if application == "teams" else "OneNote"
    return _merge_placement(result, placement, subject=label)


def close_managed_application_from_desktop(application: str) -> ToolResult:
    return close_managed_application(application)


def play_random_music_on_content_display() -> ToolResult:
    result = play_random_music()
    if not result.ok:
        return result

    track_name = str(result.data.get("selected_track_name") or "").strip()
    title_keywords = tuple(
        item
        for item in (
            track_name,
            "Media Player",
            "Windows Media Player",
            "VLC",
            "媒体播放器",
        )
        if item
    )
    configured_names = tuple(
        item.strip()
        for item in os.getenv("SMART_OFFICE_MEDIA_PLAYER_PROCESS_NAMES", "").split(",")
        if item.strip()
    )
    process_names = configured_names or _MEDIA_PROCESS_NAMES
    placement = place_window_on_content_monitor(
        process_names=process_names,
        pids=_pid_values(result),
        title_keywords=title_keywords,
        timeout_seconds=10.0,
    )
    return _merge_placement(result, placement, subject="Media Player")


def stop_music_from_desktop() -> ToolResult:
    return stop_music()
