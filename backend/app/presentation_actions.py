from __future__ import annotations

from typing import Any, Literal

from app.models import ToolResult, VerificationResult
from app.powerpoint_bootstrap import (
    PowerPointBootstrapResult,
    ensure_powerpoint_desktop_running,
)
from app.presentation_monitor import (
    inspect_slideshow_monitor,
    place_slideshow_on_target_monitor,
)
from app.presentation_verifier import verify_presentation_tool_result
from app.tools.presentation_controller import (
    end_configured_slideshow,
    get_presentation_status,
    go_to_presentation_slide,
    next_presentation_slide,
    open_configured_presentation,
    previous_presentation_slide,
    start_configured_slideshow,
)

PresentationToolName = Literal[
    "presentation_open_configured",
    "presentation_start_slideshow",
    "presentation_next_slide",
    "presentation_previous_slide",
    "presentation_go_to_slide",
    "presentation_get_status",
    "presentation_end_slideshow",
]

PRESENTATION_TOOL_NAMES: set[str] = {
    "presentation_open_configured",
    "presentation_start_slideshow",
    "presentation_next_slide",
    "presentation_previous_slide",
    "presentation_go_to_slide",
    "presentation_get_status",
    "presentation_end_slideshow",
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


def _attach_bootstrap(
    tool_result: ToolResult,
    bootstrap: PowerPointBootstrapResult | None,
) -> ToolResult:
    if bootstrap is None:
        return tool_result
    return tool_result.model_copy(
        update={
            "data": {
                **tool_result.data,
                "powerpoint_bootstrap": bootstrap.to_dict(),
            },
            "raw": {
                **tool_result.raw,
                "powerpoint_bootstrap": bootstrap.to_dict(),
            },
        }
    )


def _merge_monitor_verification(
    name: str,
    verification: VerificationResult,
    monitor_state: dict[str, Any],
    *,
    slideshow_active: bool,
) -> VerificationResult:
    # Monitor placement is a postcondition only for actions that create or mutate
    # an active slide show. A read-only status query must still answer the user's
    # question even when the window-monitor probe is temporarily unavailable.
    monitor_required = slideshow_active and name in {
        "presentation_start_slideshow",
        "presentation_next_slide",
        "presentation_previous_slide",
        "presentation_go_to_slide",
    }
    if not monitor_required:
        return verification.model_copy(
            update={"raw": {**verification.raw, "monitor_state": monitor_state}}
        )

    monitor_ok = bool(monitor_state.get("monitor_placement_enforced"))
    message = verification.message
    if verification.ok and monitor_ok:
        message = (
            f"{message} Slide show verified on "
            f"{monitor_state.get('slideshow_monitor_device')}."
        )
    elif verification.ok:
        message = (
            f"{message} Slide show was not verified on the configured monitor "
            f"{monitor_state.get('target_monitor_device')}."
        )
    return verification.model_copy(
        update={
            "ok": verification.ok and monitor_ok,
            "message": message,
            "raw": {
                **verification.raw,
                "monitor_required": True,
                "monitor_ok": monitor_ok,
                "monitor_state": monitor_state,
            },
        }
    )


def execute_presentation_tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    """Execute one GPT Realtime-selected bounded presentation capability.

    The model selects a capability, while this service owns validation,
    PowerPoint desktop bootstrap, execution, secondary-display placement,
    state verification, and the final observed PowerPoint status. This path is
    shared by Gate 2A single actions and Gate 2B compound task steps.
    """

    clean_arguments = dict(arguments or {})
    if name not in PRESENTATION_TOOL_NAMES:
        tool_result = _invalid_tool_result(
            name,
            f"Unregistered presentation capability: {name}",
            arguments=clean_arguments,
        )
        verification = verify_presentation_tool_result(tool_result)
        return tool_result, verification, get_presentation_status()

    bootstrap: PowerPointBootstrapResult | None = None

    if name == "presentation_go_to_slide":
        unexpected = set(clean_arguments) - {"slide_number"}
        value = clean_arguments.get("slide_number")
        if unexpected:
            tool_result = _invalid_tool_result(
                name,
                f"Unexpected arguments for {name}: {sorted(unexpected)}",
                arguments=clean_arguments,
            )
        elif isinstance(value, bool) or not isinstance(value, int):
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
            bootstrap = ensure_powerpoint_desktop_running()
            tool_result = open_configured_presentation()
        elif name == "presentation_start_slideshow":
            # Starting a show may also need to open the configured file, so the
            # task/runtime path requires the same desktop bootstrap as Gate 1 API.
            bootstrap = ensure_powerpoint_desktop_running()
            tool_result = start_configured_slideshow()
        elif name == "presentation_next_slide":
            tool_result = next_presentation_slide()
        elif name == "presentation_previous_slide":
            tool_result = previous_presentation_slide()
        elif name == "presentation_get_status":
            tool_result = get_presentation_status()
        else:
            tool_result = end_configured_slideshow()

    tool_result = _attach_bootstrap(tool_result, bootstrap)

    placement: dict[str, Any] | None = None
    if name == "presentation_start_slideshow" and tool_result.ok:
        placement = place_slideshow_on_target_monitor()
        tool_result = tool_result.model_copy(
            update={
                "data": {
                    **tool_result.data,
                    "requested_state": {
                        **dict(tool_result.data.get("requested_state") or {}),
                        "target_monitor_device": placement.get("target_monitor_device"),
                    },
                },
                "raw": {**tool_result.raw, "monitor_placement": placement},
            }
        )

    verification = verify_presentation_tool_result(tool_result)
    status = get_presentation_status()
    monitor_state = placement or inspect_slideshow_monitor()
    merged_status = {**status.data, **monitor_state}
    status = status.model_copy(update={"data": merged_status})
    verification = _merge_monitor_verification(
        name,
        verification,
        monitor_state,
        slideshow_active=bool(merged_status.get("slideshow_active")),
    )
    return tool_result, verification, status
