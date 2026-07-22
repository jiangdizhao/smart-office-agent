from __future__ import annotations

from typing import Any, Literal

from app.models import ToolResult, VerificationResult
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


def execute_presentation_tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    """Execute one GPT Realtime-selected Gate 2A presentation capability.

    The model selects a bounded capability, while this service owns validation,
    execution, state verification, and the final observed PowerPoint status.
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
            tool_result = open_configured_presentation()
        elif name == "presentation_start_slideshow":
            tool_result = start_configured_slideshow()
        elif name == "presentation_next_slide":
            tool_result = next_presentation_slide()
        elif name == "presentation_previous_slide":
            tool_result = previous_presentation_slide()
        elif name == "presentation_get_status":
            tool_result = get_presentation_status()
        else:
            tool_result = end_configured_slideshow()

    verification = verify_presentation_tool_result(tool_result)
    status = get_presentation_status()
    return tool_result, verification, status
