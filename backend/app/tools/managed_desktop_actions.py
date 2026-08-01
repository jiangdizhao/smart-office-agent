from __future__ import annotations

import csv
import ctypes
import os
import subprocess
import time
from io import StringIO
from typing import Any, Iterable

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
        "process_names": ("ms-teams.exe", "teams.exe"),
        "title_keywords": ("Microsoft Teams", "Teams"),
    },
    "onenote": {
        "process_names": ("onenote.exe",),
        "title_keywords": ("OneNote",),
    },
}

_MEDIA_PROCESS_NAMES = (
    "Microsoft.Media.Player.exe",
    "MediaPlayer.exe",
    "Music.UI.exe",
    "wmplayer.exe",
    "vlc.exe",
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


def _place_real_window(
    *,
    process_names: Iterable[str],
    pids: Iterable[int],
    title_keywords: Iterable[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    # Prefer a titled top-level window. Process-only matching can select a Teams
    # helper/background HWND. The fallback remains for localized window titles.
    placement = place_window_on_content_monitor(
        title_keywords=title_keywords,
        timeout_seconds=timeout_seconds,
    )
    if placement.get("placement_verified"):
        return placement

    fallback = place_window_on_content_monitor(
        process_names=process_names,
        pids=pids,
        title_keywords=title_keywords,
        timeout_seconds=timeout_seconds,
    )
    fallback["title_only_attempt"] = placement
    return fallback


def _merge_placement(
    result: ToolResult,
    placement: dict[str, Any],
    *,
    subject: str,
) -> ToolResult:
    placement_ok = bool(placement.get("placement_verified"))
    launch_verified = bool(result.ok and result.data.get("verified", True) is not False)
    message = (
        f"{subject} opened and was moved to DISPLAY2."
        if placement_ok
        else (
            f"{subject} opened. DISPLAY2 placement was requested, but window placement "
            "diagnostics were inconclusive."
        )
    )
    # Window placement is now best-effort metadata. A successful application launch
    # must not be converted into failure because Windows cannot report maximized or
    # foreground state reliably.
    return result.model_copy(
        update={
            "ok": launch_verified,
            "message": message,
            "data": {
                **result.data,
                "launch_verified": launch_verified,
                "window_placement": placement,
                "window_placement_verified": placement_ok,
                "window_placement_required_for_success": False,
                "maximization_required": False,
                "verified": launch_verified,
                "content_monitor_device": (placement.get("target_monitor") or {}).get(
                    "device"
                ),
                "status_scope": "managed_application_launch_with_best_effort_display2",
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
                "message": f"{application} opened; no DISPLAY2 placement specification exists.",
                "data": {
                    **result.data,
                    "verified": True,
                    "window_placement_required_for_success": False,
                },
            }
        )

    # Always reactivate. Teams commonly leaves only a background/tray process, and
    # open_managed_application() historically treated that as already open.
    reactivation = _reactivate_application(application)
    time.sleep(0.6)
    placement = _place_real_window(
        process_names=spec["process_names"],
        pids=_pid_values(result),
        title_keywords=spec["title_keywords"],
        timeout_seconds=8.0,
    )

    # Retry once only when the visible main HWND has not appeared yet. No maximize
    # operation or maximize verification is performed.
    if not placement.get("placement_verified"):
        second_reactivation = _reactivate_application(application)
        time.sleep(0.8)
        placement = _place_real_window(
            process_names=spec["process_names"],
            pids=_pid_values(result),
            title_keywords=spec["title_keywords"],
            timeout_seconds=8.0,
        )
        placement["second_reactivation"] = second_reactivation

    placement["reactivation"] = reactivation
    label = "Microsoft Teams" if application == "teams" else "OneNote"
    return _merge_placement(result, placement, subject=label)


def close_managed_application_from_desktop(application: str) -> ToolResult:
    return close_managed_application(application)


def _configured_media_process_names() -> tuple[str, ...]:
    configured = tuple(
        item.strip()
        for item in os.getenv("SMART_OFFICE_MEDIA_PLAYER_PROCESS_NAMES", "").split(",")
        if item.strip()
    )
    return configured or _MEDIA_PROCESS_NAMES


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
    placement = _place_real_window(
        process_names=_configured_media_process_names(),
        pids=_pid_values(result),
        title_keywords=title_keywords,
        timeout_seconds=10.0,
    )
    return _merge_placement(result, placement, subject="Media Player")


def _send_media_stop_key() -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        vk_media_stop = 0xB2
        keyeventf_keyup = 0x0002
        user32.keybd_event(vk_media_stop, 0, 0, 0)
        user32.keybd_event(vk_media_stop, 0, keyeventf_keyup, 0)
        return True
    except Exception:
        return False


def _running_named_processes(names: Iterable[str]) -> dict[int, str]:
    expected = {str(name).strip().casefold() for name in names if str(name).strip()}
    if os.name != "nt" or not expected:
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
    running: dict[int, str] = {}
    for row in csv.reader(StringIO(output)):
        if len(row) < 2 or str(row[0]).strip().casefold() not in expected:
            continue
        try:
            running[int(str(row[1]).replace(",", "").strip())] = str(row[0]).strip()
        except ValueError:
            continue
    return running


def _force_close_media_players(names: Iterable[str]) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for name in names:
        try:
            completed = subprocess.run(
                ["taskkill", "/IM", str(name), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            attempts.append(
                {
                    "image": str(name),
                    "return_code": completed.returncode,
                    "stdout": completed.stdout[-400:],
                    "stderr": completed.stderr[-400:],
                }
            )
        except Exception as exc:
            attempts.append(
                {"image": str(name), "error": f"{type(exc).__name__}: {exc}"}
            )

    deadline = time.monotonic() + 5.0
    remaining = _running_named_processes(names)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.2)
        remaining = _running_named_processes(names)
    return {"attempts": attempts, "remaining_processes": remaining}


def stop_music_from_desktop() -> ToolResult:
    # First use the session-aware close path, then independently send the Windows
    # media-stop key and terminate every configured player image. The old path could
    # track the short-lived Shell launcher PID instead of the real Media Player PID.
    session_result = stop_music()
    media_stop_sent = _send_media_stop_key()
    process_names = _configured_media_process_names()
    force = _force_close_media_players(process_names)
    remaining = force["remaining_processes"]
    verified = not remaining

    return ToolResult(
        tool_name="system_music_stop",
        ok=verified,
        message=(
            "Music playback stopped and all configured media-player processes were closed."
            if verified
            else "Music stop was requested, but a media-player process is still running."
        ),
        expected_process_names=list(process_names),
        data={
            "action": "stop",
            "verified": verified,
            "media_stop_key_sent": media_stop_sent,
            "session_close_result": session_result.model_dump(mode="json"),
            "force_close": force,
            "remaining_processes": remaining,
            "status_scope": "managed_music_only",
        },
        raw={
            "session_close_result": session_result.model_dump(mode="json"),
            "force_close": force,
        },
    )
