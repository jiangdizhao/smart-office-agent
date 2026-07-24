from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.event_bus import event_bus
from app.models import PlannedStep, TaskSession, ToolResult, VerificationResult
from app.presentation_actions import execute_presentation_tool_call
from app.state_store import state_store
from app.task_graph import build_task_graph, task_graph_event_data

PLAN_TOOL_NAME = "presentation_plan"
# Kept as an import-compatible alias while Gate 2B callers migrate to the
# unified plan terminology.
SEQUENCE_TOOL_NAME = PLAN_TOOL_NAME
MAX_PLAN_STEPS = 8
ALLOWED_SEQUENCE_ACTIONS = {
    "presentation_open_configured",
    "presentation_start_slideshow",
    "presentation_next_slide",
    "presentation_previous_slide",
    "presentation_go_to_slide",
    "presentation_get_status",
    "presentation_end_slideshow",
}

_ACTION_TITLES = {
    "presentation_open_configured": "Open the configured presentation",
    "presentation_start_slideshow": "Start the slide show",
    "presentation_next_slide": "Move to the next slide",
    "presentation_previous_slide": "Move to the previous slide",
    "presentation_go_to_slide": "Go to the requested slide",
    "presentation_get_status": "Inspect the presentation status",
    "presentation_end_slideshow": "End the slide show",
}


def _validation_error(message: str, arguments: dict[str, Any]) -> ToolResult:
    return ToolResult(
        tool_name=PLAN_TOOL_NAME,
        ok=False,
        message=message,
        expected_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
        data={
            "execution_mode": "rejected",
            "arguments": arguments,
            "requested_state": {},
        },
        raw={"validation_error": message},
    )


def _step_arguments(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    name = item.get("name")
    unexpected = set(item) - {"name", "slide_number", "slide_target"}
    if unexpected:
        return None, f"Unexpected presentation-plan step fields: {sorted(unexpected)}"

    if name not in ALLOWED_SEQUENCE_ACTIONS:
        return None, f"Unsupported presentation-plan action: {name}"

    if name == "presentation_go_to_slide":
        has_number = "slide_number" in item
        has_target = "slide_target" in item
        if has_number == has_target:
            return None, (
                "presentation_go_to_slide requires exactly one of slide_number "
                "or slide_target."
            )
        if has_target:
            slide_target = item.get("slide_target")
            if slide_target != "last":
                return None, "slide_target must be 'last'."
            return {"slide_target": "last"}, None

        slide_number = item.get("slide_number")
        if isinstance(slide_number, bool) or not isinstance(slide_number, int):
            return None, "presentation_go_to_slide requires an integer slide_number."
        if slide_number < 1:
            return None, "slide_number must be at least 1."
        return {"slide_number": slide_number}, None

    if "slide_number" in item or "slide_target" in item:
        return None, f"{name} does not accept slide_number or slide_target."
    return {}, None


def validate_sequence_arguments(
    arguments: dict[str, Any] | None,
) -> tuple[list[PlannedStep] | None, ToolResult | None]:
    """Validate the single GPT Realtime presentation-plan schema.

    One step represents a single action. Two to eight steps represent a compound
    request. Natural-language interpretation is not performed here; Backend only
    validates the structured plan selected by GPT Realtime.
    """

    clean_arguments = dict(arguments or {})
    unexpected = set(clean_arguments) - {"steps"}
    if unexpected:
        return None, _validation_error(
            f"Unexpected presentation-plan arguments: {sorted(unexpected)}",
            clean_arguments,
        )

    raw_steps = clean_arguments.get("steps")
    if not isinstance(raw_steps, list):
        return None, _validation_error("steps must be an array.", clean_arguments)
    if len(raw_steps) < 1:
        return None, _validation_error(
            "A presentation plan requires at least one step.",
            clean_arguments,
        )
    if len(raw_steps) > MAX_PLAN_STEPS:
        return None, _validation_error(
            f"A presentation plan may contain at most {MAX_PLAN_STEPS} steps.",
            clean_arguments,
        )

    planned_steps: list[PlannedStep] = []
    for index, raw_item in enumerate(raw_steps, start=1):
        if not isinstance(raw_item, dict):
            return None, _validation_error(
                f"Presentation-plan step {index} must be an object.",
                clean_arguments,
            )
        name = raw_item.get("name")
        args, error = _step_arguments(raw_item)
        if error is not None or not isinstance(name, str):
            return None, _validation_error(
                f"Presentation-plan step {index} is invalid: {error or 'name is required.'}",
                clean_arguments,
            )
        planned_steps.append(
            PlannedStep(
                index=index,
                title=_ACTION_TITLES[name],
                tool_name=name,
                args=args or {},
                requires_confirmation=False,
            )
        )

    return planned_steps, None


def create_presentation_sequence_task(
    user_request: str,
    planned_steps: list[PlannedStep],
) -> TaskSession:
    task_graph = build_task_graph(planned_steps)
    task_graph.source = "gpt_realtime_presentation_plan"
    task = state_store.create_task(
        user_request=user_request,
        execute=True,
        task_graph=task_graph,
    )
    event_bus.publish(
        task_id=task.task_id,
        event_type="task_created",
        message="Gate 2B presentation-plan task created.",
        data={
            "execute": True,
            "step_count": len(task_graph.steps),
            "source": task_graph.source,
        },
    )
    event_bus.publish(
        task_id=task.task_id,
        event_type="planning",
        message="GPT Realtime plan validated and converted into a bounded task graph.",
        data=task_graph_event_data(task_graph),
    )
    return task


def _task_cancelled(task_id: str) -> bool:
    task = state_store.get_task(task_id)
    return task is None or task.status == "cancelled"


def _cancel_remaining(task_id: str, message: str) -> None:
    state_store.update_pending_steps(task_id, "cancelled", message=message)
    state_store.set_status(task_id, "cancelled", summary=message)


def _merge_step_result(
    result: ToolResult,
    verification: VerificationResult,
    status: ToolResult,
) -> ToolResult:
    return result.model_copy(
        update={
            "data": {
                **result.data,
                "presentation_status": dict(status.data),
                "verification": verification.model_dump(mode="json"),
            },
            "raw": {
                **result.raw,
                "gate2b_presentation_plan": True,
            },
        }
    )


async def run_presentation_sequence_task(task_id: str) -> None:
    task = state_store.get_task(task_id)
    if task is None:
        return

    try:
        state_store.set_status(
            task_id,
            "running",
            summary="Presentation-plan task is running.",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="planning",
            message="Gate 2B presentation-plan execution started.",
            data={"execute": True, "mode": "presentation_plan"},
        )

        for step in task.steps:
            if _task_cancelled(task_id):
                _cancel_remaining(task_id, "Presentation task cancelled by user.")
                return

            state_store.update_step(
                task_id,
                step.step_id,
                "running",
                message="Presentation step started.",
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
                },
            )

            if step.tool_name is None:
                result = _validation_error(
                    f"Presentation-plan step {step.index} has no executable tool.",
                    {"step_id": step.step_id},
                )
                verification = VerificationResult(
                    ok=False,
                    message="Presentation step has no executable tool.",
                    process_ok=None,
                    window_ok=None,
                    expected_process_names=[],
                    found_process_names=[],
                    expected_window_keywords=[],
                    found_window_titles=[],
                    require_window_match=False,
                    raw={"invalid_step": True},
                    checked_at=datetime.now(UTC),
                )
                status = ToolResult(
                    tool_name="presentation_get_status",
                    ok=True,
                    message="Status unavailable for invalid step.",
                    data={},
                )
            else:
                result, verification, status = await asyncio.to_thread(
                    execute_presentation_tool_call,
                    step.tool_name,
                    step.args,
                )

            merged_result = _merge_step_result(result, verification, status)
            state_store.update_step(
                task_id,
                step.step_id,
                "failed" if not result.ok else "verifying",
                message=result.message,
                result=merged_result,
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="tool_result",
                message=result.message,
                data=merged_result.model_dump(mode="json"),
            )

            if _task_cancelled(task_id):
                _cancel_remaining(task_id, "Presentation task cancelled by user.")
                return

            state_store.update_step(
                task_id,
                step.step_id,
                "succeeded" if verification.ok else "failed",
                message=verification.message,
                result=merged_result,
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
                    message="Not executed because an earlier presentation step failed.",
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
                    message=f"Gate 2B presentation plan failed at step {step.index}.",
                    data={
                        "tool_result": merged_result.model_dump(mode="json"),
                        "verification_result": verification.model_dump(mode="json"),
                    },
                )
                return

            await asyncio.sleep(0)

        if _task_cancelled(task_id):
            _cancel_remaining(task_id, "Presentation task cancelled by user.")
            return

        state_store.set_status(
            task_id,
            "completed",
            summary=f"Completed and verified {len(task.steps)} presentation steps.",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="completed",
            message="Gate 2B presentation plan completed and verified.",
            data={"steps": len(task.steps), "mode": "presentation_plan"},
        )
    except Exception as exc:
        state_store.update_pending_steps(
            task_id,
            "cancelled",
            message="Not executed because the presentation task raised an exception.",
        )
        state_store.set_status(
            task_id,
            "failed",
            summary=f"Presentation-plan task failed: {exc}",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="error",
            message=f"Presentation-plan task failed: {exc}",
            data={"error": str(exc)},
        )
