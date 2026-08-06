import csv
import subprocess
import time
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from app.models import ToolResult, VerificationResult


_MANAGED_SCOPED_TOOLS = {
    "system_open_teams",
    "system_close_teams",
    "system_open_onenote",
    "system_close_onenote",
    "system_music_play_random",
    "system_music_stop",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _list_process_names() -> list[str]:
    names = _list_process_names_with_pywin32()
    if names:
        return names

    try:
        output = subprocess.check_output(
            ["tasklist", "/fo", "csv", "/nh"],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return []

    rows = csv.reader(StringIO(output))
    return [row[0] for row in rows if row]


def _list_process_names_with_pywin32() -> list[str]:
    try:
        import win32api
        import win32process
    except Exception:
        return []

    process_query_limited_information = 0x1000
    names: list[str] = []

    for pid in win32process.EnumProcesses():
        if pid == 0:
            continue

        handle = None
        try:
            handle = win32api.OpenProcess(process_query_limited_information, False, pid)
            executable = win32process.GetModuleFileNameEx(handle, 0)
            if executable:
                names.append(Path(executable).name)
        except Exception:
            continue
        finally:
            if handle is not None:
                try:
                    win32api.CloseHandle(handle)
                except Exception:
                    pass

    return names


def _find_matching_processes(expected_process_names: list[str]) -> list[str]:
    expected = {name.lower() for name in expected_process_names}
    return [
        process_name
        for process_name in _list_process_names()
        if process_name.lower() in expected
    ]


def _list_window_titles() -> list[str]:
    try:
        import win32gui
    except Exception:
        return []

    titles: list[str] = []

    def collect_window(hwnd, _extra):
        if not win32gui.IsWindowVisible(hwnd):
            return

        title = win32gui.GetWindowText(hwnd).strip()
        if title:
            titles.append(title)

    try:
        win32gui.EnumWindows(collect_window, None)
    except Exception:
        return []

    return titles


def _find_matching_windows(expected_window_keywords: list[str]) -> list[str]:
    normalized_keywords = [
        keyword.lower()
        for keyword in expected_window_keywords
        if keyword and keyword.strip()
    ]
    if not normalized_keywords:
        return []

    matches = []
    for title in _list_window_titles():
        lower_title = title.lower()
        if any(keyword in lower_title for keyword in normalized_keywords):
            matches.append(title)
    return matches


def _verify_managed_scoped_result(tool_result: ToolResult) -> VerificationResult:
    verified = tool_result.data.get("verified") is True
    action = str(tool_result.data.get("action") or "")
    application = str(tool_result.data.get("application") or "")
    selected_track = str(
        tool_result.data.get("selected_track_name")
        or tool_result.data.get("selected_track")
        or ""
    )
    subject = application or selected_track or tool_result.tool_name
    message = (
        f"Verified managed {action or 'system'} state for {subject}."
        if verified
        else f"Managed {action or 'system'} state for {subject} was not verified."
    )
    found_processes = [
        str(name)
        for name in dict(tool_result.data.get("processes") or {}).values()
    ]
    found_windows = [
        str(item.get("title") or "")
        for item in list(tool_result.data.get("windows") or [])
        if isinstance(item, dict) and item.get("title")
    ]
    if not found_processes:
        found_processes = [
            str(name)
            for name in dict(tool_result.data.get("detected_processes") or {}).values()
        ]
    if not found_windows:
        found_windows = [
            str(item.get("title") or "")
            for item in list(tool_result.data.get("detected_windows") or [])
            if isinstance(item, dict) and item.get("title")
        ]
    return VerificationResult(
        ok=verified,
        message=message,
        process_ok=verified if action == "open" else None,
        window_ok=None,
        expected_process_names=tool_result.expected_process_names,
        found_process_names=found_processes,
        expected_window_keywords=tool_result.expected_window_keywords,
        found_window_titles=found_windows,
        require_window_match=False,
        checked_at=_utc_now(),
        raw={
            "verification_type": "managed_scoped_state",
            "tool_name": tool_result.tool_name,
            "status_scope": tool_result.data.get("status_scope"),
            "action": action,
            "application": application or None,
            "selected_track": selected_track or None,
            "already_running": tool_result.data.get("already_running"),
            "already_stopped": tool_result.data.get("already_stopped"),
            "force_close_used": tool_result.data.get("force_close_used"),
            "remaining_processes": tool_result.data.get("remaining_processes"),
            "remaining_windows": tool_result.data.get("remaining_windows"),
            "unrelated_status_queries_skipped": tool_result.data.get(
                "unrelated_status_queries_skipped",
                ["powerpoint", "brightness", "outlook", "artifacts"],
            ),
        },
    )


def verify_tool_result(
    tool_result: ToolResult,
    *,
    process_timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.25,
    require_window_match: bool = False,
) -> VerificationResult:
    if tool_result.tool_name.startswith("presentation_"):
        from app.presentation_verifier import verify_presentation_tool_result

        return verify_presentation_tool_result(
            tool_result,
            timeout_seconds=process_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    if not tool_result.ok:
        return VerificationResult(
            ok=False,
            message="Tool execution failed; verification skipped.",
            process_ok=False,
            window_ok=None,
            expected_process_names=tool_result.expected_process_names,
            expected_window_keywords=tool_result.expected_window_keywords,
            require_window_match=require_window_match,
            checked_at=_utc_now(),
            raw={"tool_ok": tool_result.ok},
        )

    if tool_result.tool_name in _MANAGED_SCOPED_TOOLS:
        return _verify_managed_scoped_result(tool_result)

    expected_process_names = tool_result.expected_process_names
    if not expected_process_names:
        return VerificationResult(
            ok=True,
            message="No process expectation provided; verification treated as passed.",
            process_ok=None,
            window_ok=None,
            expected_window_keywords=tool_result.expected_window_keywords,
            require_window_match=require_window_match,
            checked_at=_utc_now(),
            raw={"skipped": "no_expected_process_names"},
        )

    deadline = time.monotonic() + process_timeout_seconds
    found_process_names: list[str] = []
    while time.monotonic() <= deadline:
        found_process_names = _find_matching_processes(expected_process_names)
        if found_process_names:
            break
        time.sleep(poll_interval_seconds)

    process_ok = bool(found_process_names)
    found_window_titles = _find_matching_windows(tool_result.expected_window_keywords)
    window_ok = bool(found_window_titles) if tool_result.expected_window_keywords else None
    ok = process_ok and (window_ok if require_window_match else True)

    if ok:
        message = "Process verification passed."
        if window_ok:
            message = "Process and window verification passed."
        elif tool_result.expected_window_keywords:
            message = "Process verification passed; matching window title was not found."
    elif not process_ok:
        message = "Expected process was not detected."
    else:
        message = "Expected window title was not detected."

    return VerificationResult(
        ok=ok,
        message=message,
        process_ok=process_ok,
        window_ok=window_ok,
        expected_process_names=expected_process_names,
        found_process_names=found_process_names,
        expected_window_keywords=tool_result.expected_window_keywords,
        found_window_titles=found_window_titles,
        require_window_match=require_window_match,
        checked_at=_utc_now(),
        raw={
            "process_timeout_seconds": process_timeout_seconds,
            "poll_interval_seconds": poll_interval_seconds,
        },
    )
