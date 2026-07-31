from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models import ToolResult, VerificationResult
from app.office_artifacts import (
    generate_presentation_summary,
    latest_summary_path,
    office_artifact_status,
)
from app.outlook_drafts import create_outlook_summary_draft, outlook_draft_status
from app.outlook_send import send_latest_outlook_draft
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

logger = logging.getLogger(__name__)

OFFICE_TOOL_NAMES: set[str] = {
    *PRESENTATION_TOOL_NAMES,
    "system_get_status",
    "system_set_volume",
    "system_adjust_volume",
    "system_set_brightness",
    "system_adjust_brightness",
    "office_generate_presentation_summary",
    "outlook_create_summary_draft",
    "outlook_send_approved_draft",
}


def _log_office_failure(
    *,
    name: str,
    arguments: dict[str, Any],
    internal_task_id: Any,
    result: ToolResult,
    verification: VerificationResult,
    status: ToolResult,
) -> None:
    if result.ok and verification.ok:
        return
    payload = {
        "event": "office_action_failed",
        "tool": name,
        "task_id": str(internal_task_id) if internal_task_id else None,
        "arguments": arguments,
        "tool_result": {
            "ok": result.ok,
            "message": result.message,
            "data": result.data,
            "raw": result.raw,
        },
        "verification_result": {
            "ok": verification.ok,
            "message": verification.message,
            "raw": verification.raw,
        },
        "observed_status": status.data,
    }
    logger.error(
        "OFFICE ACTION FAILURE\n%s",
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
    )


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
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
        },
        checked_at=datetime.now(UTC),
    )


def get_office_status() -> ToolResult:
    presentation = get_presentation_status()
    system = get_system_control_status()
    artifacts = office_artifact_status()
    outlook = outlook_draft_status()
    data = {
        "outlook_draft_configured": outlook.get("outlook_draft_configured"),
        "sender_account_email": outlook.get("sender_account_email"),
        "default_recipient_key": outlook.get("default_recipient_key"),
        "recipient_key": outlook.get("recipient_key"),
        "recipient_name": outlook.get("recipient_name"),
        "recipient_email": outlook.get("recipient_email"),
        "recipient_catalog": outlook.get("recipient_catalog"),
        "allowed_recipient_keys": outlook.get("allowed_recipient_keys"),
        "email_send_enabled": False,
        "approval_gated_email_send_enabled": True,
        "unrestricted_email_send_enabled": False,
        **dict(presentation.data),
        "volume_percent": system.data.get("volume_percent"),
        "brightness_percent": system.data.get("brightness_percent"),
        "presentation": dict(presentation.data),
        "system": dict(system.data),
        "artifacts": artifacts,
        "outlook": outlook,
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
        recipient_key = result.data.get("recipient_key")
        recipient_email = result.data.get("recipient_email")
        ok = bool(
            entry_id
            and recipient_key
            and recipient_email
            and result.data.get("outlook_draft_created") is True
            and result.data.get("outlook_draft_verified") is True
            and result.data.get("sent") is False
            and result.data.get("email_send_enabled") is False
        )
        return _verification(
            ok=ok,
            message=(
                f"Verified a Classic Outlook draft for allowlisted recipient {recipient_key}."
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
                "recipient_key": recipient_key,
                "recipient_name": result.data.get("recipient_name"),
                "recipient_email": recipient_email,
                "sent": result.data.get("sent"),
                "email_send_enabled": False,
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
            },
        )
    if result.tool_name == "outlook_send_approved_draft":
        recipient_key = result.data.get("recipient_key")
        ok = bool(
            recipient_key
            and result.data.get("send_invoked") is True
            and result.data.get("sent") is True
            and result.data.get("draft_notice_removed") is True
            and result.data.get("approval_gated_email_send_enabled") is True
            and result.data.get("unrestricted_email_send_enabled") is False
        )
        return _verification(
            ok=ok,
            message=(
                f"Verified removal of the draft notice and Outlook send acceptance for {recipient_key}."
                if ok
                else "The approved Outlook send was not verified."
            ),
            result=result,
            observed={
                **observed,
                "source_outlook_draft_entry_id": result.data.get(
                    "source_outlook_draft_entry_id"
                ),
                "recipient_key": recipient_key,
                "recipient_name": result.data.get("recipient_name"),
                "recipient_email": result.data.get("recipient_email"),
                "draft_notice_removed": result.data.get("draft_notice_removed"),
                "send_invoked": result.data.get("send_invoked"),
                "sent": result.data.get("sent"),
                "delivery_confirmed": result.data.get("delivery_confirmed"),
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
            },
        )
    return _verification(
        ok=False,
        message=f"No office verifier is registered for {result.tool_name}.",
        result=result,
        observed=observed,
    )


def _task_snapshot(internal_task_id: Any) -> dict[str, Any] | None:
    if not internal_task_id:
        return None
    task = state_store.get_task(str(internal_task_id))
    return task.model_dump(mode="json") if task is not None else None


def _draft_with_summary_prerequisite(
    *,
    clean: dict[str, Any],
    internal_task_id: Any,
) -> ToolResult:
    language = "en" if clean.get("language") == "en" else "zh"
    summary_result: ToolResult | None = None
    current_summary = latest_summary_path()
    if current_summary is None or not current_summary.is_file():
        summary_result = generate_presentation_summary(
            language=language,
            task_snapshot=_task_snapshot(internal_task_id),
        )
        if not summary_result.ok:
            return ToolResult(
                tool_name="outlook_create_summary_draft",
                ok=False,
                message=(
                    "Outlook draft prerequisite failed because the current presentation "
                    f"summary could not be generated: {summary_result.message}"
                ),
                artifacts=list(summary_result.artifacts),
                data={
                    "execution_mode": "failed_prerequisite",
                    "requested_state": {
                        "outlook_draft_created": True,
                        "recipient_key": clean.get("recipient_key"),
                    },
                    "failure_stage": "summary_generation_prerequisite",
                    "summary_prerequisite_generated": False,
                    "summary_prerequisite_result": summary_result.model_dump(mode="json"),
                    "recipient_key": clean.get("recipient_key"),
                    "email_send_enabled": False,
                    "approval_gated_email_send_enabled": True,
                    "unrestricted_email_send_enabled": False,
                    "sent": False,
                },
                raw={
                    "failure_stage": "summary_generation_prerequisite",
                    "summary_prerequisite_generated": False,
                },
            )
    result = create_outlook_summary_draft(
        language=language,
        subject=(str(clean.get("subject")) if clean.get("subject") else None),
        recipient_key=(
            str(clean.get("recipient_key")) if clean.get("recipient_key") else None
        ),
        display=True,
    )
    if summary_result is None:
        return result
    return result.model_copy(
        update={
            "artifacts": list(dict.fromkeys([*summary_result.artifacts, *result.artifacts])),
            "data": {
                **result.data,
                "summary_prerequisite_generated": True,
                "summary_prerequisite_result": summary_result.model_dump(mode="json"),
            },
            "raw": {
                **result.raw,
                "summary_prerequisite_generated": True,
            },
        }
    )


def execute_office_tool_call_direct(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    clean = dict(arguments or {})
    internal_task_id = clean.pop("_task_id", None)
    if name in PRESENTATION_TOOL_NAMES:
        result, verification, status = execute_presentation_tool_call(name, clean)
        _log_office_failure(
            name=name,
            arguments=clean,
            internal_task_id=internal_task_id,
            result=result,
            verification=verification,
            status=status,
        )
        return result, verification, status
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
        verification = _verify_non_presentation(result, status)
        _log_office_failure(
            name=name,
            arguments=clean,
            internal_task_id=internal_task_id,
            result=result,
            verification=verification,
            status=status,
        )
        return result, verification, status
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
        result = generate_presentation_summary(
            language="en" if clean.get("language") == "en" else "zh",
            task_snapshot=_task_snapshot(internal_task_id),
        )
    elif name == "outlook_create_summary_draft":
        result = _draft_with_summary_prerequisite(
            clean=clean,
            internal_task_id=internal_task_id,
        )
    else:
        result = send_latest_outlook_draft(
            recipient_key=(
                str(clean.get("recipient_key")) if clean.get("recipient_key") else None
            )
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
                "source_outlook_draft_entry_id",
                "draft_notice_removed",
                "send_invoked",
                "delivery_confirmed",
                "approval_gated_email_send_enabled",
                "unrestricted_email_send_enabled",
                "recipient_key",
                "recipient_name",
                "recipient_email",
                "sender_account_email",
                "subject",
                "email_send_enabled",
                "sent",
                "summary_prerequisite_generated",
            }
        }
    )
    status = status.model_copy(update={"data": status_data})
    verification = _verify_non_presentation(result, status)
    _log_office_failure(
        name=name,
        arguments=clean,
        internal_task_id=internal_task_id,
        result=result,
        verification=verification,
        status=status,
    )
    return result, verification, status


def execute_office_tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    from app.office_worker_process import execute_office_tool_isolated

    return execute_office_tool_isolated(name, dict(arguments or {}))
