from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models import ToolResult, VerificationResult
from app.tools.system_controller import adjust_system_volume, set_system_volume

_VOLUME_TOOLS = {"system_set_volume", "system_adjust_volume"}


def is_scoped_volume_tool(tool_name: str) -> bool:
    return tool_name in _VOLUME_TOOLS


def _failure(
    tool_name: str,
    message: str,
    requested_state: dict[str, Any],
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    result = ToolResult(
        tool_name=tool_name,
        ok=False,
        message=message,
        data={
            "execution_mode": "scoped_windows_core_audio",
            "requested_state": requested_state,
        },
    )
    verification = VerificationResult(
        ok=False,
        message=message,
        checked_at=datetime.now(UTC),
        raw={
            "verification_type": "scoped_windows_core_audio",
            "requested_state": requested_state,
        },
    )
    status = ToolResult(
        tool_name="office_get_status",
        ok=False,
        message="Volume status is unavailable because the scoped action failed.",
        data={
            "status_scope": "volume_only",
            "volume_percent": None,
            "requested_state": requested_state,
        },
    )
    return result, verification, status


def execute_scoped_volume_action(
    tool_name: str,
    args: dict[str, Any],
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    """Execute and verify volume without touching unrelated Office capabilities."""

    try:
        if tool_name == "system_set_volume":
            target = int(args["value_percent"])
            result = set_system_volume(target)
        elif tool_name == "system_adjust_volume":
            delta = int(args["delta_percent"])
            result = adjust_system_volume(delta)
        else:
            return _failure(
                tool_name,
                f"Unsupported scoped volume tool: {tool_name}",
                {},
            )
    except (KeyError, TypeError, ValueError) as exc:
        return _failure(
            tool_name,
            f"Invalid volume arguments: {type(exc).__name__}: {exc}",
            {},
        )

    requested = dict(result.data.get("requested_state") or {})
    expected = requested.get("volume_percent")
    actual = result.data.get("volume_percent")
    verified = bool(
        result.ok
        and isinstance(expected, int)
        and not isinstance(expected, bool)
        and isinstance(actual, int)
        and not isinstance(actual, bool)
        and abs(actual - expected) <= 1
    )
    verification = VerificationResult(
        ok=verified,
        message=(
            f"Verified system volume at {actual}%."
            if verified
            else f"Expected system volume {expected}%, observed {actual}%."
        ),
        checked_at=datetime.now(UTC),
        raw={
            "verification_type": "scoped_windows_core_audio",
            "status_scope": "volume_only",
            "requested_state": requested,
            "observed_volume_percent": actual,
            "unrelated_status_queries_skipped": [
                "powerpoint",
                "brightness",
                "outlook",
                "artifacts",
            ],
        },
    )
    status = ToolResult(
        tool_name="office_get_status",
        ok=verified,
        message=(
            "Scoped Windows volume status inspected."
            if verified
            else "Scoped Windows volume status did not match the request."
        ),
        data={
            "status_scope": "volume_only",
            "volume_percent": actual,
            "volume": result.data.get("volume"),
            "requested_state": requested,
            "unrelated_status_queries_skipped": [
                "powerpoint",
                "brightness",
                "outlook",
                "artifacts",
            ],
        },
    )
    return result, verification, status
