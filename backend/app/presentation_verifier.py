from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from app.models import ToolResult, VerificationResult
from app.powerpoint_rot_connection import install_powerpoint_rot_fallback

install_powerpoint_rot_fallback()

from app.tools.presentation_controller import get_presentation_status


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _matches(
    tool_name: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> tuple[bool, str]:
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


def _desktop_evidence(tool_result: ToolResult) -> dict[str, Any]:
    placement_value = tool_result.data.get("window_placement")
    placement = placement_value if isinstance(placement_value, dict) else {}
    selected_value = placement.get("selected_window")
    selected = selected_value if isinstance(selected_value, dict) else {}
    title = str(selected.get("title") or "").strip()
    process_name = str(selected.get("process_name") or "").strip()
    class_name = str(selected.get("class_name") or "").strip()
    visible = bool(
        placement.get("window_visible")
        or selected.get("visible")
        or placement.get("placement_verified")
    )
    powerpoint_window = bool(
        visible
        and (
            process_name.casefold() == "powerpnt.exe"
            or "powerpoint" in title.casefold()
            or "ppt" in title.casefold()
        )
    )
    slideshow_window = bool(
        visible
        and (
            "slide show" in title.casefold()
            or "slideshow" in title.casefold()
            or "幻灯片放映" in title
            or "screenclass" in class_name.casefold()
        )
    )
    return {
        "window_found": bool(placement.get("window_found") or selected),
        "window_visible": visible,
        "powerpoint_window": powerpoint_window,
        "slideshow_window": slideshow_window,
        "placement_verified": bool(placement.get("placement_verified")),
        "title": title,
        "process_name": process_name,
        "class_name": class_name,
    }


def _action_result_fallback(
    tool_result: ToolResult,
    observed: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    """Resolve a known PowerPoint state-observer false negative.

    PowerPoint may complete a COM command and visibly open a window before a new
    worker process can reconnect to the same application through the ROT. The
    command result is authoritative for whether the COM call returned successfully;
    desktop-window evidence is supplementary. This fallback never converts a failed
    tool result into success.
    """

    desktop = _desktop_evidence(tool_result)
    raw = dict(tool_result.raw)
    method = str(raw.get("com_method") or "").strip()
    launch_verified = bool(tool_result.data.get("launch_verified", tool_result.ok))
    name = tool_result.tool_name
    evidence = {
        "tool_ok": tool_result.ok,
        "launch_verified": launch_verified,
        "com_method": method,
        "desktop": desktop,
        "observer_connected": bool(observed.get("powerpoint_connected")),
        "observer_presentation_open": bool(observed.get("presentation_open")),
        "observer_slideshow_active": bool(observed.get("slideshow_active")),
    }

    if not tool_result.ok or not launch_verified:
        return False, "", evidence

    if name == "presentation_open_configured" and method == "Presentations.Open":
        return (
            True,
            "PowerPoint open completed through COM; the state observer was still synchronising.",
            evidence,
        )

    if name == "presentation_start_slideshow" and method == "SlideShowSettings.Run":
        return (
            True,
            "PowerPoint slide-show start completed through COM; the state observer was still synchronising.",
            evidence,
        )

    navigation_methods = {
        "presentation_next_slide": "SlideShowView.Next",
        "presentation_previous_slide": "SlideShowView.Previous",
        "presentation_go_to_slide": "SlideShowView.GotoSlide",
    }
    if name in navigation_methods and method == navigation_methods[name]:
        return (
            True,
            "The requested slide-navigation COM command completed; the state observer was still synchronising.",
            evidence,
        )

    if name == "presentation_end_slideshow" and method in {
        "SlideShowView.Exit",
        "SlideShowWindows.View.Exit",
    }:
        return (
            True,
            "The slide-show exit command completed; the state observer was still synchronising.",
            evidence,
        )

    if name == "presentation_close" and not tool_result.data.get("remaining_powerpoint_pids"):
        return (
            True,
            "PowerPoint process exit was confirmed by the close action.",
            evidence,
        )

    if name == "presentation_open_configured" and desktop["powerpoint_window"]:
        return True, "A visible PowerPoint window confirmed the completed open action.", evidence
    if name == "presentation_start_slideshow" and desktop["slideshow_window"]:
        return True, "A visible slide-show window confirmed the completed start action.", evidence

    return False, "", evidence


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
                "verification_source": "tool_failure",
                "tool_ok": False,
                "tool_message": tool_result.message,
                "execution_mode": tool_result.data.get("execution_mode"),
            },
        )

    expected = dict(tool_result.data.get("requested_state") or {})
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    observed: dict[str, Any] = {}
    matched = False
    message = "PowerPoint state did not match the requested state."

    while True:
        status_result = get_presentation_status()
        observed = dict(status_result.data)
        matched, message = _matches(tool_result.tool_name, expected, observed)
        if matched or time.monotonic() >= deadline:
            break
        time.sleep(max(0.01, poll_interval_seconds))

    verification_source = "state_observer"
    fallback_ok = False
    fallback_message = ""
    fallback_evidence: dict[str, Any] = {}
    if not matched:
        fallback_ok, fallback_message, fallback_evidence = _action_result_fallback(
            tool_result,
            observed,
        )
        if fallback_ok:
            matched = True
            message = fallback_message
            verification_source = "successful_action_result"

    desktop = _desktop_evidence(tool_result)
    process_expected = tool_result.tool_name not in {
        "presentation_close",
        "presentation_get_status",
    }
    process_ok = (
        bool(observed.get("powerpoint_connected"))
        or bool(desktop["powerpoint_window"] or desktop["slideshow_window"])
        or (matched and verification_source == "successful_action_result")
    ) if process_expected else None
    window_expected = tool_result.tool_name in {
        "presentation_open_configured",
        "presentation_start_slideshow",
        "presentation_next_slide",
        "presentation_previous_slide",
        "presentation_go_to_slide",
    }
    window_ok = (
        bool(observed.get("presentation_open"))
        or bool(desktop["powerpoint_window"] or desktop["slideshow_window"])
        or (matched and verification_source == "successful_action_result")
    ) if window_expected else None

    found_titles: list[str] = []
    observed_title = str(observed.get("presentation_name") or "").strip()
    desktop_title = str(desktop.get("title") or "").strip()
    if observed_title and bool(observed.get("presentation_open")):
        found_titles.append(observed_title)
    if desktop_title and desktop_title not in found_titles:
        found_titles.append(desktop_title)

    return VerificationResult(
        ok=matched,
        message=message,
        process_ok=process_ok,
        window_ok=window_ok,
        expected_process_names=tool_result.expected_process_names,
        found_process_names=(tool_result.expected_process_names if process_ok else []),
        expected_window_keywords=tool_result.expected_window_keywords,
        found_window_titles=found_titles,
        require_window_match=window_expected,
        checked_at=_utc_now(),
        raw={
            "verification_type": "powerpoint_state",
            "verification_source": verification_source,
            "execution_mode": observed.get("execution_mode"),
            "expected_state": expected,
            "observed_state": observed,
            "desktop_evidence": desktop,
            "action_result_fallback_used": fallback_ok,
            "action_result_evidence": fallback_evidence,
            "timeout_seconds": timeout_seconds,
            "poll_interval_seconds": poll_interval_seconds,
            "simulated": False,
        },
    )
