from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.event_bus import event_bus
from app.models import PlannedStep, TaskSession, ToolResult, VerificationResult
from app.office_actions import OFFICE_TOOL_NAMES, execute_office_tool_call
from app.presentation_config import presentation_config
from app.state_store import state_store
from app.task_graph import build_task_graph, task_graph_event_data

OFFICE_PLAN_TOOL_NAME = "office_plan"
MAX_OFFICE_PLAN_STEPS = 8

_ACTION_TITLES = {
    "presentation_open_configured": "Open the configured presentation",
    "presentation_start_slideshow": "Start the slide show",
    "presentation_next_slide": "Move to the next slide",
    "presentation_previous_slide": "Move to the previous slide",
    "presentation_go_to_slide": "Go to the requested slide",
    "presentation_get_status": "Inspect the presentation status",
    "presentation_end_slideshow": "End the slide show",
    "system_get_status": "Inspect system volume and brightness",
    "system_set_volume": "Set the system volume",
    "system_adjust_volume": "Adjust the system volume",
    "system_set_brightness": "Set the display brightness",
    "system_adjust_brightness": "Adjust the display brightness",
    "office_generate_presentation_summary": "Generate a presentation summary",
    "outlook_create_summary_draft": "Create a Classic Outlook summary draft",
    "outlook_send_approved_draft": "Send the latest verified Outlook draft",
}


def _validation_error(message: str, arguments: dict[str, Any]) -> ToolResult:
    return ToolResult(
        tool_name=OFFICE_PLAN_TOOL_NAME,
        ok=False,
        message=message,
        expected_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
        data={
            "execution_mode": "rejected",
            "arguments": arguments,
            "requested_state": {},
            "email_send_enabled": False,
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
        },
        raw={
            "validation_error": message,
            "email_send_enabled": False,
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
        },
    )


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _validated_recipient_key(value: Any, *, default: bool) -> tuple[str | None, str | None]:
    if value is None:
        if not default:
            return None, None
        value = presentation_config.default_recipient_key
    if not isinstance(value, str) or not value.strip():
        return None, "recipient_key must be a non-empty string."
    try:
        recipient = presentation_config.resolve_recipient(value)
    except ValueError as exc:
        return None, str(exc)
    return recipient.key, None


def _step_arguments(
    item: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool, str | None]:
    name = item.get("name")
    if name not in OFFICE_TOOL_NAMES:
        return None, False, f"Unsupported office-plan action: {name}"

    if name == "presentation_go_to_slide":
        unexpected = set(item) - {"name", "slide_number", "slide_target"}
        if unexpected:
            return None, False, f"Unexpected fields: {sorted(unexpected)}"
        has_number = "slide_number" in item
        has_target = "slide_target" in item
        if has_number == has_target:
            return (
                None,
                False,
                "presentation_go_to_slide requires exactly one of slide_number or slide_target.",
            )
        if has_target:
            if item.get("slide_target") != "last":
                return None, False, "slide_target must be 'last'."
            return {"slide_target": "last"}, False, None
        slide_number = _integer(item.get("slide_number"))
        if slide_number is None or slide_number < 1:
            return None, False, "slide_number must be an integer of at least 1."
        return {"slide_number": slide_number}, False, None

    if name in {"system_set_volume", "system_set_brightness"}:
        unexpected = set(item) - {"name", "value_percent"}
        if unexpected:
            return None, False, f"Unexpected fields: {sorted(unexpected)}"
        value = _integer(item.get("value_percent"))
        if value is None or not 0 <= value <= 100:
            return None, False, "value_percent must be an integer from 0 to 100."
        return {"value_percent": value}, False, None

    if name in {"system_adjust_volume", "system_adjust_brightness"}:
        unexpected = set(item) - {"name", "delta_percent"}
        if unexpected:
            return None, False, f"Unexpected fields: {sorted(unexpected)}"
        delta = _integer(item.get("delta_percent"))
        if delta is None or delta == 0 or not -100 <= delta <= 100:
            return None, False, "delta_percent must be a non-zero integer from -100 to 100."
        return {"delta_percent": delta}, False, None

    if name == "office_generate_presentation_summary":
        unexpected = set(item) - {"name", "language"}
        if unexpected:
            return None, False, f"Unexpected fields: {sorted(unexpected)}"
        language = item.get("language", "zh")
        if language not in {"zh", "en"}:
            return None, False, "language must be 'zh' or 'en'."
        return {"language": language}, False, None

    if name == "outlook_create_summary_draft":
        unexpected = set(item) - {
            "name",
            "language",
            "subject",
            "summary_source",
            "recipient_key",
        }
        if unexpected:
            return None, True, f"Unexpected fields: {sorted(unexpected)}"
        language = item.get("language", "zh")
        if language not in {"zh", "en"}:
            return None, True, "language must be 'zh' or 'en'."
        if item.get("summary_source", "latest") != "latest":
            return None, True, "summary_source must be 'latest'."
        recipient_key, recipient_error = _validated_recipient_key(
            item.get("recipient_key"),
            default=True,
        )
        if recipient_error:
            return None, True, recipient_error
        args: dict[str, Any] = {
            "language": language,
            "summary_source": "latest",
            "recipient_key": recipient_key,
        }
        subject = item.get("subject")
        if subject is not None:
            if not isinstance(subject, str) or not subject.strip() or len(subject) > 180:
                return None, True, "subject must be a non-empty string of at most 180 characters."
            args["subject"] = " ".join(subject.split())
        return args, True, None

    if name == "outlook_send_approved_draft":
        unexpected = set(item) - {"name", "draft_source", "recipient_key"}
        if unexpected:
            return None, True, f"Unexpected fields: {sorted(unexpected)}"
        if item.get("draft_source", "latest_verified") != "latest_verified":
            return None, True, "draft_source must be 'latest_verified'."
        recipient_key, recipient_error = _validated_recipient_key(
            item.get("recipient_key"),
            default=False,
        )
        if recipient_error:
            return None, True, recipient_error
        args: dict[str, Any] = {"draft_source": "latest_verified"}
        if recipient_key:
            args["recipient_key"] = recipient_key
        return args, True, None

    unexpected = set(item) - {"name"}
    if unexpected:
        return None, False, f"{name} does not accept fields: {sorted(unexpected)}"
    return {}, False, None


def validate_office_plan(
    arguments: dict[str, Any] | None,
) -> tuple[list[PlannedStep] | None, ToolResult | None]:
    clean = dict(arguments or {})
    unexpected = set(clean) - {"steps"}
    if unexpected:
        return None, _validation_error(
            f"Unexpected office-plan arguments: {sorted(unexpected)}",
            clean,
        )

    raw_steps = clean.get("steps")
    if not isinstance(raw_steps, list):
        return None, _validation_error("steps must be an array.", clean)
    if not 1 <= len(raw_steps) <= MAX_OFFICE_PLAN_STEPS:
        return None, _validation_error(
            f"An office plan requires 1 to {MAX_OFFICE_PLAN_STEPS} steps.",
            clean,
        )

    planned_steps: list[PlannedStep] = []
    latest_draft_recipient_key: str | None = None
    for index, raw_item in enumerate(raw_steps, start=1):
        if not isinstance(raw_item, dict):
            return None, _validation_error(
                f"Office-plan step {index} must be an object.",
                clean,
            )
        name = raw_item.get("name")
        args, requires_confirmation, error = _step_arguments(raw_item)
        if error is not None or not isinstance(name, str):
            return None, _validation_error(
                f"Office-plan step {index} is invalid: {error or 'name is required.'}",
                clean,
            )
        step_args = args or {}
        if name == "outlook_create_summary_draft":
            latest_draft_recipient_key = str(step_args["recipient_key"])
        elif name == "outlook_send_approved_draft" and latest_draft_recipient_key:
            send_recipient_key = step_args.get("recipient_key")
            if send_recipient_key and send_recipient_key != latest_draft_recipient_key:
                return None, _validation_error(
                    "An Outlook send step in the same plan must target the same recipient_key "
                    "as the preceding draft step.",
                    clean,
                )
            step_args["recipient_key"] = latest_draft_recipient_key
        planned_steps.append(
            PlannedStep(
                index=index,
                title=_ACTION_TITLES[name],
                tool_name=name,
                args=step_args,
                requires_confirmation=requires_confirmation,
            )
        )
    return planned_steps, None


def create_office_task(user_request: str, planned_steps: list[PlannedStep]) -> TaskSession:
    graph = build_task_graph(planned_steps)
    graph.source = "gpt_realtime_office_plan"
    task = state_store.create_task(user_request=user_request, execute=True, task_graph=graph)
    event_bus.publish(
        task_id=task.task_id,
        event_type="task_created",
        message="Office workflow task created.",
        data={
            "execute": True,
            "step_count": len(graph.steps),
            "source": graph.source,
            "approval_required": any(step.requires_confirmation for step in graph.steps),
            "email_send_enabled": False,
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
        },
    )
    event_bus.publish(
        task_id=task.task_id,
        event_type="planning",
        message="GPT Realtime office plan validated and converted into a bounded task graph.",
        data=task_graph_event_data(graph),
    )
    return task


def _task_cancelled(task_id: str) -> bool:
    task = state_store.get_task(task_id)
    return task is None or task.status == "cancelled"


def _cancel_remaining(task_id: str, message: str) -> None:
    state_store.update_pending_steps(task_id, "cancelled", message=message)
    state_store.set_status(task_id, "cancelled", summary=message)
    event_bus.publish(
        task_id=task_id,
        event_type="cancelled",
        message=message,
        data={},
    )


async def _approval_action(task_id: str, step: Any) -> str:
    if not step.requires_confirmation:
        return "approve"

    is_send = step.tool_name == "outlook_send_approved_draft"
    recipient_key = step.args.get("recipient_key")
    recipient = None
    if recipient_key:
        try:
            recipient = presentation_config.resolve_recipient(str(recipient_key))
        except ValueError:
            recipient = None
    recipient_label = (
        f"{recipient.name} <{recipient.email}>" if recipient else "the latest verified recipient"
    )
    waiting_message = (
        f"A second approval is required before sending the verified Outlook draft to {recipient_label}."
        if is_send
        else f"Approval is required before creating a Classic Outlook draft for {recipient_label}."
    )
    reason = (
        "The second approval removes the draft-only notice, saves and re-verifies the "
        f"fixed sender and selected allowlisted recipient {recipient_label}, then invokes Outlook Send()."
        if is_send
        else "Creating an Outlook draft writes the generated summary to the signed-in "
        f"local Outlook mailbox for selected allowlisted recipient {recipient_label} and opens the draft window."
    )

    state_store.set_status(
        task_id,
        "waiting_approval",
        summary=f"Waiting for approval on step {step.index}.",
    )
    state_store.update_step(
        task_id,
        step.step_id,
        "waiting_approval",
        message=waiting_message,
    )
    event_bus.publish(
        task_id=task_id,
        step_id=step.step_id,
        event_type="approval_required",
        message=f"Approval required for step {step.index}: {step.title}",
        data={
            "step_index": step.index,
            "title": step.title,
            "tool_name": step.tool_name,
            "recipient_key": recipient.key if recipient else recipient_key,
            "recipient_name": recipient.name if recipient else None,
            "recipient_email": recipient.email if recipient else None,
            "actions": ["approve", "cancel", "skip", "takeover"],
            "reason": reason,
            "approval_stage": "send" if is_send else "draft",
            "email_send_enabled": False,
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
        },
    )

    while True:
        if _task_cancelled(task_id):
            return "cancel"
        approval = state_store.consume_approval(task_id, step.step_id)
        if approval is not None:
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="approval_resolved",
                message=f"Approval action received: {approval.action}.",
                data=approval.model_dump(mode="json"),
            )
            return approval.action
        await asyncio.sleep(0.25)


def _merge_step_result(
    result: ToolResult,
    verification: VerificationResult,
    status: ToolResult,
) -> ToolResult:
    status_data = dict(status.data)
    merged_data: dict[str, Any] = {
        **result.data,
        "office_status": status_data,
        "verification": verification.model_dump(mode="json"),
    }
    if "presentation_open" in status_data:
        merged_data["presentation_status"] = {
            key: status_data.get(key)
            for key in (
                "powerpoint_connected",
                "presentation_open",
                "presentation_name",
                "presentation_path",
                "slideshow_active",
                "current_slide",
                "total_slides",
                "target_monitor_device",
                "slideshow_monitor_device",
                "monitor_placement_enforced",
            )
        }
    return result.model_copy(
        update={
            "data": merged_data,
            "raw": {
                **result.raw,
                "office_plan": True,
                "email_send_enabled": False,
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
            },
        }
    )


async def run_office_task(task_id: str) -> None:
    task = state_store.get_task(task_id)
    if task is None:
        return

    try:
        state_store.set_status(task_id, "running", summary="Office workflow is running.")
        event_bus.publish(
            task_id=task_id,
            event_type="planning",
            message="Bounded Office workflow execution started.",
            data={
                "execute": True,
                "mode": "office_plan",
                "email_send_enabled": False,
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
            },
        )

        for step in task.steps:
            if _task_cancelled(task_id):
                _cancel_remaining(task_id, "Office task cancelled by user.")
                return

            approval = await _approval_action(task_id, step)
            if approval == "skip":
                state_store.update_step(
                    task_id,
                    step.step_id,
                    "skipped",
                    message="Step skipped by user.",
                )
                state_store.set_status(task_id, "running", summary="Office workflow is running.")
                continue
            if approval in {"cancel", "takeover"}:
                _cancel_remaining(
                    task_id,
                    "Manual takeover requested."
                    if approval == "takeover"
                    else "Office task cancelled by user.",
                )
                return

            state_store.set_status(task_id, "running", summary="Office workflow is running.")
            state_store.update_step(
                task_id,
                step.step_id,
                "running",
                message="Office step started.",
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="step_started",
                message=f"Executing step {step.index}: {step.title}",
                data={
                    "tool_name": step.tool_name,
                    "args": step.args,
                    "plan_index": step.index,
                    "requires_confirmation": step.requires_confirmation,
                },
            )

            if step.tool_name is None:
                result = _validation_error(
                    f"Office-plan step {step.index} has no executable tool.",
                    {"step_id": step.step_id},
                )
                verification = VerificationResult(
                    ok=False,
                    message="Office step has no executable tool.",
                    checked_at=datetime.now(UTC),
                )
                status = ToolResult(
                    tool_name="office_get_status",
                    ok=False,
                    message="Office status unavailable for invalid step.",
                    data={},
                )
            else:
                runtime_args = dict(step.args)
                if step.tool_name == "office_generate_presentation_summary":
                    runtime_args["_task_id"] = task_id
                result, verification, status = await asyncio.to_thread(
                    execute_office_tool_call,
                    step.tool_name,
                    runtime_args,
                )

            merged = _merge_step_result(result, verification, status)
            state_store.update_step(
                task_id,
                step.step_id,
                "failed" if not result.ok else "verifying",
                message=result.message,
                result=merged,
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="tool_result",
                message=result.message,
                data=merged.model_dump(mode="json"),
            )

            if _task_cancelled(task_id):
                _cancel_remaining(task_id, "Office task cancelled by user.")
                return

            state_store.update_step(
                task_id,
                step.step_id,
                "succeeded" if verification.ok else "failed",
                message=verification.message,
                result=merged,
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="verification_result",
                message=verification.message,
                data=verification.model_dump(mode="json"),
            )

            if not result.ok or not verification.ok:
                state_store.update_pending_steps(
                    task_id,
                    "cancelled",
                    message="Not executed because an earlier office step failed.",
                )
                state_store.set_status(
                    task_id,
                    "failed",
                    summary=(
                        f"Step {step.index} failed: "
                        f"{result.message if not result.ok else verification.message}"
                    ),
                )
                event_bus.publish(
                    task_id=task_id,
                    step_id=step.step_id,
                    event_type="error",
                    message=f"Office plan failed at step {step.index}.",
                    data={
                        "tool_result": merged.model_dump(mode="json"),
                        "verification_result": verification.model_dump(mode="json"),
                    },
                )
                return
            await asyncio.sleep(0)

        if _task_cancelled(task_id):
            _cancel_remaining(task_id, "Office task cancelled by user.")
            return

        final_task = state_store.get_task(task_id)
        completed_steps = len(final_task.steps) if final_task else len(task.steps)
        state_store.set_status(
            task_id,
            "completed",
            summary=f"Completed and verified {completed_steps} office steps.",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="completed",
            message="Office plan completed and verified.",
            data={
                "steps": completed_steps,
                "mode": "office_plan",
                "email_send_enabled": False,
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
            },
        )
    except Exception as exc:
        state_store.update_pending_steps(
            task_id,
            "cancelled",
            message="Not executed because the office task raised an exception.",
        )
        state_store.set_status(
            task_id,
            "failed",
            summary=f"Office plan failed: {exc}",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="error",
            message=f"Office plan failed: {exc}",
            data={"error": str(exc)},
        )
