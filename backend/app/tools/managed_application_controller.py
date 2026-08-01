from __future__ import annotations

import csv
import json
import os
import random
import shlex
import shutil
import subprocess
import time
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any, Iterable

from app.models import ToolResult

SUPPORTED_APPLICATIONS = {"teams", "onenote"}
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".wma", ".aac", ".ogg"}

_APPLICATION_PROCESS_NAMES: dict[str, tuple[str, ...]] = {
    "teams": ("ms-teams.exe", "teams.exe"),
    "onenote": ("onenote.exe",),
}
_APPLICATION_WINDOW_KEYWORDS: dict[str, tuple[str, ...]] = {
    "teams": ("Microsoft Teams", "Teams"),
    "onenote": ("OneNote",),
}
_DEFAULT_MEDIA_PROCESS_NAMES = (
    "Microsoft.Media.Player.exe",
    "MediaPlayer.exe",
    "Music.UI.exe",
    "wmplayer.exe",
    "vlc.exe",
)
_FORCE_CLOSE_ENV = "SMART_OFFICE_FORCE_CLOSE_MANAGED_APPS"
_MUSIC_DIRECTORY_ENV = "SMART_OFFICE_MUSIC_DIRECTORY"
_MEDIA_PROCESS_ENV = "SMART_OFFICE_MEDIA_PLAYER_PROCESS_NAMES"
_MEDIA_WINDOW_ENV = "SMART_OFFICE_MEDIA_PLAYER_WINDOW_KEYWORDS"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _runtime_directory() -> Path:
    path = _project_root() / "data" / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _media_session_path() -> Path:
    return _runtime_directory() / "managed_media_session.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _env_enabled(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "off", "no"}


def _configured_list(name: str, defaults: Iterable[str]) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(values) if values else tuple(defaults)


def _normalise_process_name(value: str) -> str:
    return Path(value.strip().strip('"')).name.casefold()


def _list_processes() -> dict[int, str]:
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

    processes: dict[int, str] = {}
    for row in csv.reader(StringIO(output)):
        if len(row) < 2:
            continue
        try:
            pid = int(str(row[1]).replace(",", "").strip())
        except ValueError:
            continue
        processes[pid] = str(row[0]).strip()
    return processes


def _matching_processes(names: Iterable[str]) -> dict[int, str]:
    expected = {_normalise_process_name(name) for name in names}
    return {
        pid: name
        for pid, name in _list_processes().items()
        if _normalise_process_name(name) in expected
    }


def _list_windows() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        import win32gui
        import win32process
    except Exception:
        return []

    windows: list[dict[str, Any]] = []

    def collect(hwnd: int, _extra: object) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            if not title:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            windows.append({"hwnd": int(hwnd), "pid": int(pid), "title": title})
        except Exception:
            return

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return []
    return windows


def _matching_windows(
    *,
    keywords: Iterable[str] = (),
    pids: Iterable[int] = (),
) -> list[dict[str, Any]]:
    clean_keywords = [keyword.casefold() for keyword in keywords if keyword.strip()]
    pid_set = {int(pid) for pid in pids}
    matches: list[dict[str, Any]] = []
    for window in _list_windows():
        title = str(window.get("title") or "")
        pid = int(window.get("pid") or 0)
        keyword_match = bool(
            clean_keywords and any(keyword in title.casefold() for keyword in clean_keywords)
        )
        pid_match = bool(pid_set and pid in pid_set)
        if keyword_match or pid_match:
            matches.append(window)
    return matches


def _wait_for_presence(
    *,
    process_names: Iterable[str],
    window_keywords: Iterable[str],
    timeout_seconds: float = 8.0,
) -> tuple[dict[int, str], list[dict[str, Any]]]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        processes = _matching_processes(process_names)
        windows = _matching_windows(keywords=window_keywords)
        if processes or windows or time.monotonic() >= deadline:
            return processes, windows
        time.sleep(0.25)


def _wait_for_absence(
    *,
    process_names: Iterable[str] = (),
    pids: Iterable[int] = (),
    window_keywords: Iterable[str] = (),
    timeout_seconds: float = 5.0,
) -> tuple[dict[int, str], list[dict[str, Any]]]:
    expected_names = {_normalise_process_name(name) for name in process_names}
    pid_set = {int(pid) for pid in pids}
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        processes = {
            pid: name
            for pid, name in _list_processes().items()
            if pid in pid_set or _normalise_process_name(name) in expected_names
        }
        windows = _matching_windows(keywords=window_keywords, pids=pid_set)
        if (not processes and not windows) or time.monotonic() >= deadline:
            return processes, windows
        time.sleep(0.25)


def _post_close_to_windows(windows: Iterable[dict[str, Any]]) -> int:
    if os.name != "nt":
        return 0
    try:
        import win32con
        import win32gui
    except Exception:
        return 0

    count = 0
    for window in windows:
        try:
            win32gui.PostMessage(int(window["hwnd"]), win32con.WM_CLOSE, 0, 0)
            count += 1
        except Exception:
            continue
    return count


def _taskkill(*, pid: int | None = None, image_name: str | None = None, force: bool) -> bool:
    if os.name != "nt":
        return False
    command = ["taskkill"]
    if pid is not None:
        command.extend(["/PID", str(int(pid))])
    elif image_name:
        command.extend(["/IM", image_name])
    else:
        return False
    command.append("/T")
    if force:
        command.append("/F")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0
    except Exception:
        return False


def _split_configured_command(value: str) -> list[str]:
    clean = value.strip()
    if not clean:
        return []
    possible_path = Path(clean.strip('"'))
    if possible_path.exists():
        return [str(possible_path)]
    try:
        parts = shlex.split(clean, posix=False)
    except ValueError:
        return [clean]
    return [part.strip('"') for part in parts if part.strip('"')]


def _application_commands(application: str) -> list[list[str]]:
    if application not in SUPPORTED_APPLICATIONS:
        return []
    env_name = (
        "SMART_OFFICE_TEAMS_COMMAND"
        if application == "teams"
        else "SMART_OFFICE_ONENOTE_COMMAND"
    )
    commands: list[list[str]] = []
    configured = _split_configured_command(os.getenv(env_name, ""))
    if configured:
        commands.append(configured)

    executable_names = (
        ("ms-teams.exe", "Teams.exe")
        if application == "teams"
        else ("ONENOTE.EXE", "onenote.exe")
    )
    for executable_name in executable_names:
        resolved = shutil.which(executable_name)
        if resolved:
            commands.append([resolved])

    local_app_data = Path(os.getenv("LOCALAPPDATA", "")) if os.getenv("LOCALAPPDATA") else None
    program_files = [os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)")]
    candidates: list[Path] = []
    if application == "teams" and local_app_data:
        candidates.extend(
            [
                local_app_data / "Microsoft" / "WindowsApps" / "ms-teams.exe",
                local_app_data / "Microsoft" / "Teams" / "current" / "Teams.exe",
            ]
        )
    if application == "onenote":
        for base in [Path(value) for value in program_files if value]:
            candidates.extend(
                [
                    base / "Microsoft Office" / "root" / "Office16" / "ONENOTE.EXE",
                    base / "Microsoft Office" / "Office16" / "ONENOTE.EXE",
                ]
            )
    commands.extend([[str(path)] for path in candidates if path.is_file()])

    protocol = "msteams:" if application == "teams" else "onenote:"
    commands.append(["cmd", "/c", "start", "", protocol])

    deduplicated: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in commands:
        key = tuple(item.casefold() for item in command)
        if key not in seen:
            seen.add(key)
            deduplicated.append(command)
    return deduplicated


def _launch_command(command: list[str]) -> int | None:
    process = subprocess.Popen(
        command,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return int(process.pid)


def _application_label(application: str) -> str:
    return "Microsoft Teams" if application == "teams" else "OneNote"


def open_managed_application(application: str) -> ToolResult:
    application = application.strip().casefold()
    tool_name = f"system_open_{application}"
    if application not in SUPPORTED_APPLICATIONS:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Unsupported managed application: {application}",
            data={"application": application, "action": "open", "verified": False},
        )
    if os.name != "nt":
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message="Managed application control is available only on Windows.",
            data={"application": application, "action": "open", "verified": False},
        )

    process_names = _APPLICATION_PROCESS_NAMES[application]
    window_keywords = _APPLICATION_WINDOW_KEYWORDS[application]
    existing_processes, existing_windows = _wait_for_presence(
        process_names=process_names,
        window_keywords=window_keywords,
        timeout_seconds=0,
    )
    if existing_processes or existing_windows:
        return ToolResult(
            tool_name=tool_name,
            ok=True,
            message=f"{_application_label(application)} is already open.",
            expected_process_names=list(process_names),
            expected_window_keywords=list(window_keywords),
            data={
                "application": application,
                "action": "open",
                "already_running": True,
                "verified": True,
                "processes": existing_processes,
                "windows": existing_windows,
                "status_scope": "managed_application_only",
            },
        )

    errors: list[str] = []
    launched_pid: int | None = None
    launched_command: list[str] | None = None
    for command in _application_commands(application):
        try:
            launched_pid = _launch_command(command)
            launched_command = command
            break
        except Exception as exc:
            errors.append(f"{command!r}: {type(exc).__name__}: {exc}")

    if launched_command is None:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Could not launch {_application_label(application)}.",
            expected_process_names=list(process_names),
            expected_window_keywords=list(window_keywords),
            raw={"launch_errors": errors},
            data={
                "application": application,
                "action": "open",
                "verified": False,
                "status_scope": "managed_application_only",
            },
        )

    processes, windows = _wait_for_presence(
        process_names=process_names,
        window_keywords=window_keywords,
    )
    verified = bool(processes or windows)
    return ToolResult(
        tool_name=tool_name,
        ok=verified,
        message=(
            f"{_application_label(application)} opened and was verified."
            if verified
            else f"{_application_label(application)} launch was requested, but no matching process or window was detected."
        ),
        launched_pid=launched_pid,
        expected_process_names=list(process_names),
        expected_window_keywords=list(window_keywords),
        raw={"command": launched_command, "launch_errors": errors},
        data={
            "application": application,
            "action": "open",
            "already_running": False,
            "verified": verified,
            "processes": processes,
            "windows": windows,
            "status_scope": "managed_application_only",
            "unrelated_status_queries_skipped": [
                "powerpoint",
                "brightness",
                "outlook",
                "artifacts",
            ],
        },
    )


def close_managed_application(application: str) -> ToolResult:
    application = application.strip().casefold()
    tool_name = f"system_close_{application}"
    if application not in SUPPORTED_APPLICATIONS:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Unsupported managed application: {application}",
            data={"application": application, "action": "close", "verified": False},
        )
    if os.name != "nt":
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message="Managed application control is available only on Windows.",
            data={"application": application, "action": "close", "verified": False},
        )

    process_names = _APPLICATION_PROCESS_NAMES[application]
    window_keywords = _APPLICATION_WINDOW_KEYWORDS[application]
    processes = _matching_processes(process_names)
    windows = _matching_windows(keywords=window_keywords, pids=processes.keys())
    if not processes and not windows:
        return ToolResult(
            tool_name=tool_name,
            ok=True,
            message=f"{_application_label(application)} is already closed.",
            expected_process_names=list(process_names),
            expected_window_keywords=list(window_keywords),
            data={
                "application": application,
                "action": "close",
                "already_stopped": True,
                "verified": True,
                "status_scope": "managed_application_only",
            },
        )

    graceful_windows = _post_close_to_windows(windows)
    remaining_processes, remaining_windows = _wait_for_absence(
        process_names=process_names,
        window_keywords=window_keywords,
        timeout_seconds=3.0,
    )
    force_used = False
    if remaining_processes and _env_enabled(_FORCE_CLOSE_ENV, True):
        force_used = True
        for pid in list(remaining_processes):
            _taskkill(pid=pid, force=True)
        remaining_processes, remaining_windows = _wait_for_absence(
            process_names=process_names,
            window_keywords=window_keywords,
            timeout_seconds=4.0,
        )

    verified = not remaining_processes and not remaining_windows
    return ToolResult(
        tool_name=tool_name,
        ok=verified,
        message=(
            f"{_application_label(application)} closed and was verified."
            if verified
            else f"{_application_label(application)} could not be fully closed."
        ),
        expected_process_names=list(process_names),
        expected_window_keywords=list(window_keywords),
        data={
            "application": application,
            "action": "close",
            "already_stopped": False,
            "graceful_close_windows": graceful_windows,
            "force_close_used": force_used,
            "verified": verified,
            "remaining_processes": remaining_processes,
            "remaining_windows": remaining_windows,
            "status_scope": "managed_application_only",
            "unrelated_status_queries_skipped": [
                "powerpoint",
                "brightness",
                "outlook",
                "artifacts",
            ],
        },
    )


def _music_directory() -> Path:
    configured = os.getenv(_MUSIC_DIRECTORY_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (_project_root() / "data" / "music").resolve()


def _music_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
    )


def _media_process_names() -> tuple[str, ...]:
    return _configured_list(_MEDIA_PROCESS_ENV, _DEFAULT_MEDIA_PROCESS_NAMES)


def _media_window_keywords(track: Path | None = None) -> tuple[str, ...]:
    defaults = ["Media Player", "Windows Media Player", "VLC"]
    if track is not None:
        defaults.insert(0, track.stem)
    return _configured_list(_MEDIA_WINDOW_ENV, defaults)


def _write_media_session(payload: dict[str, Any]) -> None:
    path = _media_session_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_media_session() -> dict[str, Any]:
    path = _media_session_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _remove_media_session() -> None:
    try:
        _media_session_path().unlink(missing_ok=True)
    except Exception:
        pass


def _start_default_media(track: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Default media playback is available only on Windows.")
    startfile = getattr(os, "startfile", None)
    if not callable(startfile):
        raise RuntimeError("Windows Shell file association is unavailable.")
    startfile(str(track))


def play_random_music() -> ToolResult:
    tool_name = "system_music_play_random"
    directory = _music_directory()
    tracks = _music_files(directory)
    if not tracks:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"No supported music files were found in {directory}.",
            artifacts=[str(directory)],
            data={
                "action": "play_random",
                "music_directory": str(directory),
                "supported_extensions": sorted(SUPPORTED_AUDIO_EXTENSIONS),
                "verified": False,
                "status_scope": "managed_music_only",
            },
        )
    if os.name != "nt":
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message="Default media playback is available only on Windows.",
            artifacts=[str(directory)],
            data={
                "action": "play_random",
                "music_directory": str(directory),
                "verified": False,
                "status_scope": "managed_music_only",
            },
        )

    track = random.SystemRandom().choice(tracks)
    try:
        _start_default_media(track)
    except Exception as exc:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Could not open the selected music file: {type(exc).__name__}: {exc}",
            artifacts=[str(track)],
            raw={"error": str(exc)},
            data={
                "action": "play_random",
                "selected_track": str(track),
                "verified": False,
                "status_scope": "managed_music_only",
            },
        )

    process_names = _media_process_names()
    window_keywords = _media_window_keywords(track)
    processes, windows = _wait_for_presence(
        process_names=process_names,
        window_keywords=window_keywords,
        timeout_seconds=8.0,
    )
    candidate_pids = sorted(
        set(processes) | {int(window["pid"]) for window in windows}
    )
    verified = bool(processes or windows)
    session = {
        "started_at": _utc_now(),
        "selected_track": str(track),
        "selected_track_name": track.name,
        "music_directory": str(directory),
        "candidate_pids": candidate_pids,
        "detected_processes": processes,
        "detected_windows": windows,
        "player_process_names": list(process_names),
        "window_keywords": list(window_keywords),
        "shell_dispatch_succeeded": True,
        "verified": verified,
    }
    _write_media_session(session)

    return ToolResult(
        tool_name=tool_name,
        ok=True,
        message=(
            f"Random music playback started: {track.name}."
            if verified
            else f"The selected track was sent to the default media player, but the player process was not positively identified: {track.name}."
        ),
        expected_process_names=list(process_names),
        expected_window_keywords=list(window_keywords),
        artifacts=[str(track)],
        data={
            "action": "play_random",
            "selected_track": str(track),
            "selected_track_name": track.name,
            "music_directory": str(directory),
            "shell_dispatch_succeeded": True,
            "verified": verified,
            "detected_processes": processes,
            "detected_windows": windows,
            "candidate_pids": candidate_pids,
            "status_scope": "managed_music_only",
            "unrelated_status_queries_skipped": [
                "powerpoint",
                "brightness",
                "outlook",
                "artifacts",
            ],
        },
    )


def stop_music() -> ToolResult:
    tool_name = "system_music_stop"
    if os.name != "nt":
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message="Managed music control is available only on Windows.",
            data={"action": "stop", "verified": False, "status_scope": "managed_music_only"},
        )

    session = _read_media_session()
    process_names = tuple(
        str(name)
        for name in session.get("player_process_names", _media_process_names())
        if str(name).strip()
    )
    candidate_pids = {
        int(pid)
        for pid in session.get("candidate_pids", [])
        if isinstance(pid, int) or str(pid).isdigit()
    }
    track_path = Path(str(session.get("selected_track") or "")) if session.get("selected_track") else None
    window_keywords = tuple(
        str(keyword)
        for keyword in session.get("window_keywords", _media_window_keywords(track_path))
        if str(keyword).strip()
    )

    processes = _matching_processes(process_names)
    windows = _matching_windows(keywords=window_keywords, pids=candidate_pids)
    owned_processes = {
        pid: name
        for pid, name in processes.items()
        if not candidate_pids or pid in candidate_pids
    }
    if not session and not processes and not windows:
        return ToolResult(
            tool_name=tool_name,
            ok=True,
            message="Music is already stopped.",
            expected_process_names=list(process_names),
            expected_window_keywords=list(window_keywords),
            data={
                "action": "stop",
                "already_stopped": True,
                "verified": True,
                "status_scope": "managed_music_only",
            },
        )

    graceful_windows = _post_close_to_windows(windows)
    tracked_pids = candidate_pids or set(owned_processes)
    remaining_processes, remaining_windows = _wait_for_absence(
        pids=tracked_pids,
        window_keywords=window_keywords,
        timeout_seconds=2.5,
    )

    force_used = False
    if (remaining_processes or remaining_windows) and _env_enabled(_FORCE_CLOSE_ENV, True):
        force_used = True
        kill_pids = set(remaining_processes) | tracked_pids
        for pid in sorted(kill_pids):
            _taskkill(pid=pid, force=True)
        remaining_processes, remaining_windows = _wait_for_absence(
            pids=kill_pids,
            window_keywords=window_keywords,
            timeout_seconds=4.0,
        )

    verified = not remaining_processes and not remaining_windows
    if verified:
        _remove_media_session()

    return ToolResult(
        tool_name=tool_name,
        ok=verified,
        message=(
            "Music playback stopped and the managed player was closed."
            if verified
            else "The managed music player could not be fully closed."
        ),
        expected_process_names=list(process_names),
        expected_window_keywords=list(window_keywords),
        data={
            "action": "stop",
            "already_stopped": False,
            "selected_track": str(track_path) if track_path else None,
            "graceful_close_windows": graceful_windows,
            "force_close_used": force_used,
            "verified": verified,
            "remaining_processes": remaining_processes,
            "remaining_windows": remaining_windows,
            "status_scope": "managed_music_only",
            "unrelated_status_queries_skipped": [
                "powerpoint",
                "brightness",
                "outlook",
                "artifacts",
            ],
        },
    )
