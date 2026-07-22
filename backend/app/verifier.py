import csv
import subprocess
import time
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from app.models import ToolResult, VerificationResult


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
