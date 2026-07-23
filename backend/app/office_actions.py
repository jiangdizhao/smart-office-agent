from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models import ToolResult, VerificationResult
from app.office_artifacts import generate_presentation_summary, office_artifact_status
from app.outlook_drafts import create_outlook_summary_draft, outlook_draft_status
from app.presentation_actions import (
    PRESENTATION_TOOL_NAMES,
    execute_presentation_tool_call,
)
from app.state_store import state_store
from app.tools.presentation_controller import get_presentation_status
from app.tools.system_controller import (
    adjust_system_brightness,
    adjust_system_volume,
    get_system_control_status,
    set_system_brightness,
    set_system_volume,
)

OFFICE_TOOL_NAMES: set[str] = {
    *PRESENTATION_TOOL_NAMES,
    "system_get_status",
    "system_set_volume",
    "system_adjust_volume",
    "system_set_brightness",
    "system_adjust_brightness",
    "office_generate_presentation_summary",
    "outlook_create_summary_draft",
}


def _verification(
    *,
    ok: bool,
    message: str,
    result: ToolResult,
    observed: dict[str, Any],
) -> VerificationResult:
    return VerificationResult(
        ok=ok,
        message=message,
        process_ok=None,
        window_ok=None,
        expected_process_names=result.expected_process_names,
        found_process_names=result.expected_process_names if ok else [],
        expected_window_keywords=result.expected_window_keywords,
        found_window_titles=[],
        require_window_match=False,
        raw={
            "verification_type": "office_capability_state",
            "tool_name": result.tool_name,
            "requested_state": dict(result.data.get("requested_state") or {}),
            "observed_state": observed,
            "email_send_enabled": False,
        },
        checked_at=datetime.now(UTC),
    )


def get_office_status() -> ToolResult:
    presentation = get_presentation_status()
    system = get_system_control_status()
    artifacts = office_artifact_status()
    outlook = outlook_draft_status()
    data = {
        **dict(presentation.data),
        "presentation": dict(presentation.data),
        "system": dict(system.data),
        "artifacts": artifacts,
        "outlook": outlook,
        "volume_percent": system.data.get("volume_percent"),
        "brightness_percent": system.data.get("brightness_percent"),
        "outlook_draft_configured": outlook.get("outlook_draft_configured"),
        "recipient_name": outlook.get("recipient_name"),
        "recipient_email": outlook.get("recipient_email"),
        "email_send_enabled": False,
    }
    return ToolResult(
        tool_name="office_get_status",
        ok=bool(presentation.ok or system.ok),
        message="Office runtime status inspected.",
        data=data,
    )


def _verify_non_presentation(result: ToolResult, status: ToolResult) -> VerificationResult:
    if not result.ok:
        return _verification(
            ok=False,
            message=f"Office tool execution failed: {result.message}",
            result=result,
            observed=dict(status.data),
        )

    requested = dict(result.data.get("requested_state") or {})
    observed = dict(status.data)

    if result.tool_name == "system_get_status":
        return _verification(
            ok=True,
            message="System volume and brightness status was read.",
            result=result,
            observed=observed,
        )

    if result.tool_name in {"system_set_volume", "system_adjust_volume"}:
        expected = requested.get("volume_percent")
        actual = observed.get("volume_percent")
        ok = isinstance(expected, int) and isinstance(actual, int) and abs(actual - expected) <= 1
        return _verification(
            ok=ok,
            message=(
                f"Verified system volume at {actual}%."
                if ok
                else f"Expected system volume {expected}%, observed {actual}%."
            ),
            result=result,
            observed=observed,
        )

    if result.tool_name in {"system_set_brightness", "system_adjust_brightness"}:
        expected = requested.get("brightness_percent")
        actual = observed.get("brightness_percent")
        ok = isinstance(expected, int) and isinstance(actual, int) and abs(actual - expected) <= 2
        return _verification(
            ok=ok,
            message=(
                f"Verified display brightness at {actual}%."
                if ok
                else f"Expected display brightness {expected}%, observed {actual}%."
            ),
            result=result,
            observed=observed,
        )

    if result.tool_name == "office_generate_presentation_summary":
        summary_path = result.data.get("summary_path")
        path = Path(str(summary_path)).resolve() if summary_path else None
        ok = bool(path and path.is_file() and path.stat().st_size > 0)
        return _verification(
            ok=ok,
            message=(
                f"Verified presentation summary artifact: {result.data.get('summary_path_relative')}"
                if ok
                else "Presentation summary artifact was not found or was empty."
            ),
            result=result,
            observed={
                **observed,
                "summary_path": str(path) if path else None,
                "summary_exists": bool(path and path.is_file()),
            },
        )

    if result.tool_name == "outlook_create_summary_draft":
        entry_id = result.data.get("outlook_draft_entry_id")
        ok = bool(
            entry_id
            and result.data.get("outlook_draft_created") is True
            and result.data.get("outlook_draft_verified") is True
            and result.data.get("sent") is False
            and result.data.get("email_send_enabled") is False
        )
        return _verification(
            ok=ok,
            message=(
                "Verified that a Classic Outlook draft was saved, reopened by EntryID, and not sent."
                if ok
                else "Classic Outlook draft creation was not verified."
            ),
            result=result,
            observed={
                **observed,
                "outlook_draft_entry_id": entry_id,
                "outlook_draft_created": result.data.get("outlook_draft_created"),
                "outlook_draft_verified": result.data.get("outlook_draft_verified"),
                "outlook_draft_displayed": result.data.get("outlook_draft_displayed"),
                "sent": result.data.get("sent"),
                "email_send_enabled": False,
            },
        )

    return _verification(
        ok=False,
        message=f"No office verifier is registered for {result.tool_name}.",
        result=result,
        observed=observed,
    )


def execute_office_tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    clean = dict(arguments or {})
    internal_task_id = clean.pop("_task_id", None)

    if name in PRESENTATION_TOOL_NAMES:
        return execute_presentation_tool_call(name, clean)

    if name not in OFFICE_TOOL_NAMES:
        result = ToolResult(
            tool_name=name,
            ok=False,
            message=f"Unregistered office capability: {name}",
            data={
                "execution_mode": "rejected",
                "arguments": clean,
                "requested_state": {},
            },
            raw={"validation_error": f"Unregistered office capability: {name}"},
        )
        status = get_office_status()
        return result, _verify_non_presentation(result, status), status

    if name == "system_get_status":
        result = get_system_control_status()
    elif name == "system_set_volume":
        result = set_system_volume(int(clean["value_percent"]))
    elif name == "system_adjust_volume":
        result = adjust_system_volume(int(clean["delta_percent"]))
    elif name == "system_set_brightness":
        result = set_system_brightness(int(clean["value_percent"]))
    elif name == "system_adjust_brightness":
        result = adjust_system_brightness(int(clean["delta_percent"]))
    elif name == "office_generate_presentation_summary":
        task_snapshot = None
        if internal_task_id:
            task = state_store.get_task(str(internal_task_id))
            if task is not None:
                task_snapshot = task.model_dump(mode="json")
        result = generate_presentation_summary(
            language="en" if clean.get("language") == "en" else "zh",
            task_snapshot=task_snapshot,
        )
    else:
        result = create_outlook_summary_draft(
            language="en" if clean.get("language") == "en" else "zh",
            subject=(str(clean.get("subject")) if clean.get("subject") else None),
            display=True,
        )

    status = get_office_status()
    status_data = dict(status.data)
    status_data.update(
        {
            key: value
            for key, value in result.data.items()
            if key
            in {
                "volume_percent",
                "brightness_percent",
                "summary_created",
                "summary_path",
                "summary_path_relative",
                "summary_json_path",
                "summary_json_path_relative",
                "artifact_url",
                "outlook_draft_created",
                "outlook_draft_verified",
                "outlook_draft_entry_id",
                "outlook_draft_store_id",
                "outlook_draft_displayed",
                "outlook_connection_mode",
                "recipient_name",
                "recipient_email",
                "subject",
                "email_send_enabled",
                "sent",
            }
        }
    )
    status = status.model_copy(update={"data": status_data})
    verification = _verify_non_presentation(result, status)
    return result, verification, status
