from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from app.models import ToolResult, VerificationResult
from app.tools.presentation_controller import get_presentation_status


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _matches(tool_name: str, expected: dict[str, Any], observed: dict[str, Any]) -> tuple[bool, str]:
    if tool_name == "presentation_get_status":
        return True, "Presentation status query completed."

    if tool_name == "presentation_open_configured":
        ok = bool(observed.get("presentation_open"))
        return ok, "Configured presentation is open." if ok else "Configured presentation is not open."

    if tool_name == "presentation_start_slideshow":
        ok = bool(observed.get("slideshow_active"))
        return ok, "Slide show is active." if ok else "Slide show is not active."

    if tool_name in {
        "presentation_next_slide",
        "presentation_previous_slide",
        "presentation_go_to_slide",
    }:
        expected_slide = expected.get("current_slide")
        observed_slide = observed.get("current_slide")
        ok = bool(observed.get("slideshow_active")) and observed_slide == expected_slide
        if ok:
            return True, f"Verified current slide {observed_slide}."
        return False, f"Expected slide {expected_slide}, observed {observed_slide}."

    if tool_name == "presentation_end_slideshow":
        ok = not bool(observed.get("slideshow_active"))
        return ok, "Slide show is inactive." if ok else "Slide show is still active."

    if tool_name == "presentation_close":
        ok = not bool(observed.get("presentation_open"))
        return ok, "Configured presentation is closed." if ok else "Configured presentation is still open."

    return False, f"No presentation verifier is registered for {tool_name}."


def verify_presentation_tool_result(
    tool_result: ToolResult,
    *,
    timeout_seconds: float = 4.0,
    poll_interval_seconds: float = 0.15,
) -> VerificationResult:
    if not tool_result.ok:
        return VerificationResult(
            ok=False,
            message="PowerPoint tool execution failed; state verification was not attempted.",
            process_ok=False,
            window_ok=None,
            expected_process_names=tool_result.expected_process_names,
            expected_window_keywords=tool_result.expected_window_keywords,
            require_window_match=False,
            checked_at=_utc_now(),
            raw={
                "verification_type": "powerpoint_state",
                "tool_ok": False,
                "tool_message": tool_result.message,
                "execution_mode": tool_result.data.get("execution_mode"),
            },
        )

    expected = dict(tool_result.data.get("requested_state") or {})
    deadline = time.monotonic() + timeout_seconds
    observed: dict[str, Any] = {}
    matched = False
    message = "PowerPoint state did not match the requested state."

    while True:
        status_result = get_presentation_status()
        observed = dict(status_result.data)
        matched, message = _matches(tool_result.tool_name, expected, observed)
        if matched or time.monotonic() >= deadline:
            break
        time.sleep(poll_interval_seconds)

    process_expected = tool_result.tool_name not in {
        "presentation_close",
        "presentation_get_status",
    }
    process_ok = bool(observed.get("powerpoint_connected")) if process_expected else None
    window_expected = tool_result.tool_name in {
        "presentation_open_configured",
        "presentation_start_slideshow",
        "presentation_next_slide",
        "presentation_previous_slide",
        "presentation_go_to_slide",
    }
    window_ok = bool(observed.get("presentation_open")) if window_expected else None

    return VerificationResult(
        ok=matched,
        message=message,
        process_ok=process_ok,
        window_ok=window_ok,
        expected_process_names=tool_result.expected_process_names,
        found_process_names=(
            tool_result.expected_process_names
            if bool(observed.get("powerpoint_connected"))
            else []
        ),
        expected_window_keywords=tool_result.expected_window_keywords,
        found_window_titles=(
            [str(observed.get("presentation_name"))]
            if bool(observed.get("presentation_open"))
            else []
        ),
        require_window_match=window_expected,
        checked_at=_utc_now(),
        raw={
            "verification_type": "powerpoint_state",
            "execution_mode": observed.get("execution_mode"),
            "expected_state": expected,
            "observed_state": observed,
            "timeout_seconds": timeout_seconds,
            "poll_interval_seconds": poll_interval_seconds,
            "simulated": False,
        },
    )
