from __future__ import annotations

import re
import zipfile
from datetime import UTC, datetime
from typing import Any, Literal

from app.models import ToolResult, VerificationResult
from app.presentation_config import presentation_config
from app.presentation_verifier import verify_presentation_tool_result
from app.tools.presentation_controller import (
    end_configured_slideshow,
    get_presentation_status,
    go_to_presentation_slide,
    next_presentation_slide,
    previous_presentation_slide,
)
from app.tools.presentation_desktop_actions import (
    close_powerpoint_discarding_changes,
    open_configured_presentation_on_content_display,
    start_configured_slideshow_on_content_display,
)
from app.windows_window_placement import place_window_on_content_monitor

PresentationToolName = Literal[
    "presentation_open_configured",
    "presentation_start_slideshow",
    "presentation_next_slide",
    "presentation_previous_slide",
    "presentation_go_to_slide",
    "presentation_get_status",
    "presentation_end_slideshow",
    "presentation_close",
]

PRESENTATION_TOOL_NAMES: set[str] = {
    "presentation_open_configured",
    "presentation_start_slideshow",
    "presentation_next_slide",
    "presentation_previous_slide",
    "presentation_go_to_slide",
    "presentation_get_status",
    "presentation_end_slideshow",
    "presentation_close",
}

_FAST_NAVIGATION_NAMES = {
    "presentation_next_slide",
    "presentation_previous_slide",
    "presentation_go_to_slide",
}


def _invalid_tool_result(
    name: str,
    message: str,
    *,
    arguments: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        tool_name=name,
        ok=False,
        message=message,
        expected_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
        data={
            "execution_mode": "rejected",
            "arguments": arguments or {},
            "requested_state": {},
        },
        raw={"validation_error": message},
    )


def _validate_no_arguments(name: str, arguments: dict[str, Any]) -> ToolResult | None:
    if arguments:
        return _invalid_tool_result(
            name,
            f"{name} does not accept arguments.",
            arguments=arguments,
        )
    return None


def _go_to_last_slide(arguments: dict[str, Any]) -> ToolResult:
    status_before = get_presentation_status()
    total_slides = status_before.data.get("total_slides")
    if isinstance(total_slides, bool) or not isinstance(total_slides, int) or total_slides < 1:
        return _invalid_tool_result(
            "presentation_go_to_slide",
            "The final slide could not be resolved because the presentation slide count is unavailable.",
            arguments=arguments,
        )

    result = go_to_presentation_slide(total_slides)
    return result.model_copy(
        update={
            "message": (
                f"Moved to the final slide ({total_slides})."
                if result.ok
                else result.message
            ),
            "data": {
                **result.data,
                "requested_state": {
                    **dict(result.data.get("requested_state") or {}),
                    "current_slide": total_slides,
                },
                "resolved_slide_target": "last",
                "resolved_slide_number": total_slides,
            },
            "raw": {
                **result.raw,
                "resolved_slide_target": "last",
                "resolved_slide_number": total_slides,
            },
        }
    )


def _ensure_content_display(
    name: str,
    tool_result: ToolResult,
    status: ToolResult,
) -> dict[str, Any] | None:
    if name in {
        "presentation_close",
        "presentation_end_slideshow",
        "presentation_get_status",
        *_FAST_NAVIGATION_NAMES,
    }:
        return None

    existing = tool_result.data.get("window_placement")
    if isinstance(existing, dict) and existing.get("placement_verified"):
        return existing

    slideshow = name == "presentation_start_slideshow"
    title_keywords = (
        (
            "PowerPoint Slide Show",
            "PowerPoint 幻灯片放映",
            "幻灯片放映",
        )
        if slideshow
        else (presentation_config.presentation_path.name, "PowerPoint")
    )
    pid = status.data.get("powerpoint_process_id")
    pids = [pid] if isinstance(pid, int) and pid > 0 else []
    return place_window_on_content_monitor(
        process_names=["POWERPNT.EXE"],
        pids=pids,
        title_keywords=title_keywords,
        timeout_seconds=4.0,
    )


def _merge_monitor_verification(
    name: str,
    verification: VerificationResult,
    monitor_state: dict[str, Any],
    *,
    slideshow_active: bool,
) -> VerificationResult:
    monitor_relevant = slideshow_active and name == "presentation_start_slideshow"
    monitor_ok = bool(monitor_state.get("monitor_placement_enforced"))
    return verification.model_copy(
        update={
            "ok": verification.ok,
            "raw": {
                **verification.raw,
                "monitor_required": False,
                "monitor_relevant": monitor_relevant,
                "monitor_ok": monitor_ok,
                "monitor_state": monitor_state,
            },
        }
    )


def _merge_desktop_verification(
    name: str,
    verification: VerificationResult,
    placement: dict[str, Any] | None,
) -> VerificationResult:
    placement_relevant = name in {
        "presentation_open_configured",
        "presentation_start_slideshow",
    }
    placement_ok = bool(placement and placement.get("placement_verified"))
    return verification.model_copy(
        update={
            "ok": verification.ok,
            "raw": {
                **verification.raw,
                "content_display_placement_required": False,
                "content_display_placement_relevant": placement_relevant,
                "content_display_placement_ok": placement_ok,
                "content_display_placement": placement,
                "maximization_required": False,
            },
        }
    )


def _configured_slide_count() -> int | None:
    path = presentation_config.presentation_path
    if path.suffix.casefold() not in {".pptx", ".pptm", ".ppsx", ".ppsm"}:
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            return len(
                {
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                }
            ) or None
    except (OSError, zipfile.BadZipFile):
        return None


def _align_status_with_successful_action(
    name: str,
    tool_result: ToolResult,
    verification: VerificationResult,
    status: ToolResult,
) -> ToolResult:
    if (
        not verification.ok
        or verification.raw.get("verification_source") != "successful_action_result"
    ):
        return status

    data = dict(status.data)
    requested = dict(tool_result.data.get("requested_state") or {})
    placement_value = tool_result.data.get("window_placement")
    placement = placement_value if isinstance(placement_value, dict) else {}
    target = placement.get("target_monitor")
    target_device = (
        str(target.get("device") or "").strip()
        if isinstance(target, dict)
        else ""
    )
    observed_monitor = str(placement.get("observed_monitor_device") or "").strip()

    if name in {
        "presentation_open_configured",
        "presentation_start_slideshow",
        "presentation_next_slide",
        "presentation_previous_slide",
        "presentation_go_to_slide",
        "presentation_end_slideshow",
    }:
        data["powerpoint_connected"] = True
        data["presentation_open"] = True
        data["presentation_name"] = presentation_config.presentation_path.name
        data["presentation_path"] = str(presentation_config.presentation_path)
        data["total_slides"] = data.get("total_slides") or _configured_slide_count()

    if name == "presentation_open_configured":
        data["slideshow_active"] = bool(data.get("slideshow_active"))
    elif name == "presentation_start_slideshow":
        data["slideshow_active"] = True
        data["current_slide"] = requested.get("current_slide") or data.get("current_slide") or 1
    elif name in _FAST_NAVIGATION_NAMES:
        data["slideshow_active"] = True
        if isinstance(requested.get("current_slide"), int):
            data["current_slide"] = requested["current_slide"]
    elif name == "presentation_end_slideshow":
        data["slideshow_active"] = False
        data["current_slide"] = None
    elif name == "presentation_close":
        data["powerpoint_connected"] = False
        data["presentation_open"] = False
        data["slideshow_active"] = False
        data["current_slide"] = None

    if target_device:
        data["target_monitor_device"] = target_device
    if observed_monitor:
        data["powerpoint_window_monitor_device"] = observed_monitor
        if bool(data.get("slideshow_active")):
            data["slideshow_monitor_device"] = observed_monitor
    data["status_aligned_from_successful_action"] = True
    data["status_alignment_source"] = verification.raw.get("verification_source")
    return status.model_copy(update={"data": data})


def _fast_action_verification(tool_result: ToolResult) -> VerificationResult:
    return VerificationResult(
        ok=tool_result.ok,
        message=(
            "PowerPoint COM accepted the navigation action. Synchronous polling was skipped."
            if tool_result.ok
            else tool_result.message
        ),
        process_ok=True if tool_result.ok else None,
        window_ok=True if tool_result.ok else None,
        expected_process_names=list(tool_result.expected_process_names),
        expected_window_keywords=list(tool_result.expected_window_keywords),
        require_window_match=False,
        raw={
            "verification_source": "successful_action_result",
            "synchronous_polling": False,
            "window_repositioning": False,
            "exhibition_fast_navigation": True,
        },
        checked_at=datetime.now(UTC),
    )


def execute_presentation_tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    clean_arguments = dict(arguments or {})
    if name not in PRESENTATION_TOOL_NAMES:
        tool_result = _invalid_tool_result(
            name,
            f"Unregistered presentation capability: {name}",
            arguments=clean_arguments,
        )
        verification = verify_presentation_tool_result(tool_result)
        return tool_result, verification, get_presentation_status()

    if name == "presentation_go_to_slide":
        unexpected = set(clean_arguments) - {"slide_number", "slide_target"}
        has_number = "slide_number" in clean_arguments
        has_target = "slide_target" in clean_arguments
        if unexpected:
            tool_result = _invalid_tool_result(
                name,
                f"Unexpected arguments for {name}: {sorted(unexpected)}",
                arguments=clean_arguments,
            )
        elif has_number == has_target:
            tool_result = _invalid_tool_result(
                name,
                "presentation_go_to_slide requires exactly one of slide_number or slide_target.",
                arguments=clean_arguments,
            )
        elif has_target:
            target = clean_arguments.get("slide_target")
            if target != "last":
                tool_result = _invalid_tool_result(
                    name,
                    "slide_target must be 'last'.",
                    arguments=clean_arguments,
                )
            else:
                tool_result = _go_to_last_slide(clean_arguments)
        else:
            value = clean_arguments.get("slide_number")
            if isinstance(value, bool) or not isinstance(value, int):
                tool_result = _invalid_tool_result(
                    name,
                    "slide_number must be an integer.",
                    arguments=clean_arguments,
                )
            elif value < 1:
                tool_result = _invalid_tool_result(
                    name,
                    "slide_number must be at least 1.",
                    arguments=clean_arguments,
                )
            else:
                tool_result = go_to_presentation_slide(value)
    else:
        invalid = _validate_no_arguments(name, clean_arguments)
        if invalid is not None:
            tool_result = invalid
        elif name == "presentation_open_configured":
            tool_result = open_configured_presentation_on_content_display()
        elif name == "presentation_start_slideshow":
            tool_result = start_configured_slideshow_on_content_display()
        elif name == "presentation_next_slide":
            tool_result = next_presentation_slide()
        elif name == "presentation_previous_slide":
            tool_result = previous_presentation_slide()
        elif name == "presentation_get_status":
            tool_result = get_presentation_status()
        elif name == "presentation_end_slideshow":
            tool_result = end_configured_slideshow()
        else:
            tool_result = close_powerpoint_discarding_changes()

    # Slide navigation is an exhibition control, not a safety-sensitive workflow.
    # A successful COM return is the final synchronous result. One lightweight
    # status read keeps the UI current, while polling and DISPLAY2 repositioning are
    # deliberately skipped.
    if name in _FAST_NAVIGATION_NAMES:
        verification = _fast_action_verification(tool_result)
        status = get_presentation_status()
        status = _align_status_with_successful_action(name, tool_result, verification, status)
        return tool_result, verification, status

    status = get_presentation_status()
    placement = _ensure_content_display(name, tool_result, status)
    if placement is not None:
        target_monitor = placement.get("target_monitor") or {}
        placement_ok = bool(placement.get("placement_verified"))
        launch_verified = bool(tool_result.data.get("launch_verified", tool_result.ok))
        tool_result = tool_result.model_copy(
            update={
                "ok": launch_verified,
                "message": (
                    "PowerPoint action completed and the window was moved to DISPLAY2."
                    if launch_verified and placement_ok
                    else tool_result.message
                ),
                "data": {
                    **tool_result.data,
                    "launch_verified": launch_verified,
                    "window_placement": placement,
                    "window_placement_verified": placement_ok,
                    "window_placement_required_for_success": False,
                    "maximization_required": False,
                    "content_monitor_device": target_monitor.get("device"),
                    "verified": launch_verified,
                },
                "raw": {
                    **tool_result.raw,
                    "content_display_placement": placement,
                },
            }
        )
        status = status.model_copy(
            update={
                "data": {
                    **status.data,
                    "target_monitor_device": target_monitor.get("device"),
                    "content_monitor_device": target_monitor.get("device"),
                    "powerpoint_window_monitor_device": placement.get(
                        "observed_monitor_device"
                    ),
                    "slideshow_monitor_device": (
                        placement.get("observed_monitor_device")
                        if bool(status.data.get("slideshow_active"))
                        else status.data.get("slideshow_monitor_device")
                    ),
                    "monitor_placement_enforced": placement_ok,
                    "window_maximized": placement.get("window_maximized"),
                    "maximization_required": False,
                }
            }
        )

    verification = verify_presentation_tool_result(tool_result)
    verification = _merge_desktop_verification(name, verification, placement)
    status = _align_status_with_successful_action(name, tool_result, verification, status)
    return tool_result, verification, status
