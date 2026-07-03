import asyncio
from datetime import UTC, datetime

from app.event_bus import event_bus
from app.models import ApprovalAction, TaskStep, ToolResult
from app.state_store import state_store
from app.tool_registry import DEFAULT_TOOL_TIMEOUT_SECONDS, run_tool
from app.verifier import verify_tool_result


async def _wait_for_approval(task_id: str, step: TaskStep) -> ApprovalAction:
    state_store.set_status(
        task_id,
        "waiting_approval",
        summary=f"Waiting for approval on step {step.index}.",
    )
    state_store.update_step(
        task_id,
        step.step_id,
        "waiting_approval",
        message="Waiting for human approval.",
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
            "actions": ["approve", "cancel", "skip", "takeover"],
        },
    )

    while True:
        task = state_store.get_task(task_id)
        if task is None or task.status == "cancelled":
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


async def _handle_approval_gate(task_id: str, step: TaskStep) -> ApprovalAction:
    if not step.requires_confirmation:
        return "approve"
    return await _wait_for_approval(task_id, step)


def _finish_cancelled_task(task_id: str, step: TaskStep | None, message: str) -> None:
    step_id = step.step_id if step else None
    if step is not None:
        state_store.update_step(
            task_id,
            step.step_id,
            "cancelled",
            message=message,
        )
    state_store.update_pending_steps(task_id, "cancelled", message=message)
    state_store.set_status(task_id, "cancelled", summary=message)
    event_bus.publish(
        task_id=task_id,
        step_id=step_id,
        event_type="cancelled",
        message=message,
        data={},
    )


def _is_task_cancelled(task_id: str) -> bool:
    task = state_store.get_task(task_id)
    return task is None or task.status == "cancelled"


def _finish_if_cancelled(
    task_id: str,
    step: TaskStep | None,
    message: str = "Task cancelled by user.",
) -> bool:
    if not _is_task_cancelled(task_id):
        return False

    _finish_cancelled_task(task_id, step, message)
    return True


def _simulated_verification_payload(result: ToolResult) -> dict:
    return {
        "ok": True,
        "message": "Simulated verification passed. No real Windows tool was executed.",
        "process_ok": None,
        "window_ok": None,
        "expected_process_names": result.expected_process_names,
        "found_process_names": [],
        "expected_window_keywords": result.expected_window_keywords,
        "found_window_titles": [],
        "require_window_match": False,
        "raw": {"simulated": True},
        "checked_at": datetime.now(UTC).isoformat(),
        "simulated": True,
    }


async def run_task_plan_only(task_id: str) -> None:
    task = state_store.get_task(task_id)
    if task is None:
        return

    try:
        state_store.set_status(
            task_id,
            "running",
            summary="Plan-only executor simulation is running.",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="planning",
            message="Executor started plan-only simulation. No real tools will run.",
            data={"execute": task.execute, "mode": "plan_only_simulation"},
        )

        for step in task.steps:
            if _finish_if_cancelled(task_id, step):
                return

            approval_action = await _handle_approval_gate(task_id, step)
            if _finish_if_cancelled(task_id, step):
                return

            if approval_action == "skip":
                state_store.update_step(
                    task_id,
                    step.step_id,
                    "skipped",
                    message="Step skipped by user.",
                )
                continue
            if approval_action == "cancel":
                _finish_cancelled_task(task_id, step, "Task cancelled by user.")
                return
            if approval_action == "takeover":
                _finish_cancelled_task(task_id, step, "Manual takeover requested.")
                return

            state_store.set_status(
                task_id,
                "running",
                summary="Plan-only executor simulation is running.",
            )
            state_store.update_step(
                task_id,
                step.step_id,
                "running",
                message="Simulated step started.",
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="step_started",
                message=f"Simulating step {step.index}: {step.title}",
                data={
                    "tool_name": step.tool_name,
                    "requires_confirmation": step.requires_confirmation,
                },
            )

            await asyncio.sleep(0.15)
            if _finish_if_cancelled(task_id, step):
                return

            result = ToolResult(
                tool_name=step.tool_name or "reasoning_step",
                ok=True,
                message="Simulated only. No Windows tool was executed.",
                data={
                    "simulated": True,
                    "execute": False,
                    "planned_args": step.args,
                },
            )
            state_store.update_step(
                task_id,
                step.step_id,
                "verifying",
                message=result.message,
                result=result,
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="tool_result",
                message=result.message,
                data=result.model_dump(mode="json"),
            )

            if _finish_if_cancelled(task_id, step):
                return

            verification = _simulated_verification_payload(result)
            state_store.update_step(
                task_id,
                step.step_id,
                "succeeded",
                message=verification["message"],
                result=result,
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="verification_result",
                message="Simulated verification passed.",
                data=verification,
            )

        if _finish_if_cancelled(task_id, None):
            return

        state_store.set_status(
            task_id,
            "completed",
            summary="Plan-only simulation completed. No real tools were executed.",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="completed",
            message="Plan-only simulation completed.",
            data={"simulated": True, "steps": len(task.steps)},
        )
    except Exception as exc:
        state_store.set_status(
            task_id,
            "failed",
            summary=f"Plan-only simulation failed: {exc}",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="error",
            message=f"Plan-only simulation failed: {exc}",
            data={"error": str(exc)},
        )


async def run_task_with_tools(
    task_id: str,
    tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> None:
    task = state_store.get_task(task_id)
    if task is None:
        return

    try:
        state_store.set_status(
            task_id,
            "running",
            summary="Executor is running Windows Controller tools.",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="planning",
            message="Executor started Windows Controller run.",
            data={
                "execute": task.execute,
                "mode": "windows_controller",
                "tool_timeout_seconds": tool_timeout_seconds,
            },
        )

        for step in task.steps:
            if _finish_if_cancelled(task_id, step):
                return

            approval_action = await _handle_approval_gate(task_id, step)
            if _finish_if_cancelled(task_id, step):
                return

            if approval_action == "skip":
                state_store.update_step(
                    task_id,
                    step.step_id,
                    "skipped",
                    message="Step skipped by user.",
                )
                continue
            if approval_action == "cancel":
                _finish_cancelled_task(task_id, step, "Task cancelled by user.")
                return
            if approval_action == "takeover":
                _finish_cancelled_task(task_id, step, "Manual takeover requested.")
                return

            state_store.set_status(
                task_id,
                "running",
                summary="Executor is running Windows Controller tools.",
            )
            state_store.update_step(
                task_id,
                step.step_id,
                "running",
                message="Tool step started.",
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="step_started",
                message=f"Executing step {step.index}: {step.title}",
                data={
                    "tool_name": step.tool_name,
                    "requires_confirmation": step.requires_confirmation,
                    "tool_timeout_seconds": tool_timeout_seconds,
                },
            )

            if step.tool_name is None:
                result = ToolResult(
                    tool_name="reasoning_step",
                    ok=True,
                    message="No Windows tool assigned for this reasoning step.",
                    data={
                        "args": step.args,
                        "execute": True,
                        "no_tool": True,
                    },
                )
            else:
                result = await asyncio.to_thread(
                    run_tool,
                    step.tool_name,
                    step.args,
                    tool_timeout_seconds,
                )

            if _finish_if_cancelled(task_id, step):
                return

            state_store.update_step(
                task_id,
                step.step_id,
                "failed" if not result.ok else "verifying",
                message=result.message,
                result=result,
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="tool_result",
                message=result.message,
                data=result.model_dump(mode="json"),
            )

            if not result.ok:
                state_store.set_status(
                    task_id,
                    "failed",
                    summary=f"Step {step.index} failed: {result.message}",
                )
                event_bus.publish(
                    task_id=task_id,
                    step_id=step.step_id,
                    event_type="error",
                    message=f"Step {step.index} failed: {result.message}",
                    data=result.model_dump(mode="json"),
                )
                return

            if _finish_if_cancelled(task_id, step):
                return

            verification = await asyncio.to_thread(verify_tool_result, result)

            if _finish_if_cancelled(task_id, step):
                return

            state_store.update_step(
                task_id,
                step.step_id,
                "succeeded" if verification.ok else "failed",
                message=verification.message,
                result=result,
            )
            event_bus.publish(
                task_id=task_id,
                step_id=step.step_id,
                event_type="verification_result",
                message=verification.message,
                data=verification.model_dump(mode="json"),
            )

            if not verification.ok:
                state_store.set_status(
                    task_id,
                    "failed",
                    summary=f"Step {step.index} verification failed: {verification.message}",
                )
                event_bus.publish(
                    task_id=task_id,
                    step_id=step.step_id,
                    event_type="error",
                    message=f"Step {step.index} verification failed: {verification.message}",
                    data=verification.model_dump(mode="json"),
                )
                return

        if _finish_if_cancelled(task_id, None):
            return

        state_store.set_status(
            task_id,
            "completed",
            summary="Windows Controller run completed and verified.",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="completed",
            message="Windows Controller run completed and verified.",
            data={
                "steps": len(task.steps),
                "tool_timeout_seconds": tool_timeout_seconds,
            },
        )
    except Exception as exc:
        state_store.set_status(
            task_id,
            "failed",
            summary=f"Windows Controller run failed: {exc}",
        )
        event_bus.publish(
            task_id=task_id,
            event_type="error",
            message=f"Windows Controller run failed: {exc}",
            data={"error": str(exc)},
        )
