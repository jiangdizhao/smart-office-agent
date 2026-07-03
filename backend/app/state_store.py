from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from app.models import (
    ApprovalRequest,
    StepEvent,
    StepStatus,
    TaskGraph,
    TaskSession,
    TaskStatus,
    ToolResult,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class InMemoryStateStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskSession] = {}
        self._approvals: dict[tuple[str, str], ApprovalRequest] = {}
        self._lock = RLock()

    def create_task(
        self,
        *,
        user_request: str,
        execute: bool,
        task_graph: TaskGraph,
    ) -> TaskSession:
        task_id = str(uuid4())
        now = task_graph.created_at
        task = TaskSession(
            task_id=task_id,
            user_request=user_request,
            execute=execute,
            status="created",
            steps=task_graph.steps,
            created_at=now,
            updated_at=now,
            summary=f"Task graph created with {len(task_graph.steps)} planned step(s).",
        )

        with self._lock:
            self._tasks[task_id] = task

        return task

    def get_task(self, task_id: str) -> TaskSession | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[TaskSession]:
        with self._lock:
            return list(self._tasks.values())

    def set_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        summary: str | None = None,
    ) -> TaskSession | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            updated = utc_now()
            task.status = status
            task.updated_at = updated
            if summary is not None:
                task.summary = summary
            if status in {"completed", "failed", "cancelled"}:
                task.completed_at = updated
            return task

    def update_step(
        self,
        task_id: str,
        step_id: str,
        status: StepStatus,
        *,
        message: str | None = None,
        result: ToolResult | None = None,
    ) -> TaskSession | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            updated = utc_now()
            for step in task.steps:
                if step.step_id != step_id:
                    continue

                step.status = status
                step.message = message
                if result is not None:
                    step.result = result
                if status == "running" and step.started_at is None:
                    step.started_at = updated
                if status in {"succeeded", "failed", "skipped", "cancelled"}:
                    step.finished_at = updated
                task.updated_at = updated
                return task

            return task

    def update_pending_steps(
        self,
        task_id: str,
        status: StepStatus,
        *,
        message: str,
    ) -> TaskSession | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            updated = utc_now()
            for step in task.steps:
                if step.status in {"pending", "running", "waiting_approval", "verifying"}:
                    step.status = status
                    step.message = message
                    step.finished_at = updated
            task.updated_at = updated
            return task

    def append_event(self, task_id: str, event: StepEvent) -> TaskSession | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            task.events.append(event)
            task.updated_at = utc_now()
            return task

    def set_approval(
        self,
        task_id: str,
        step_id: str,
        approval: ApprovalRequest,
    ) -> None:
        with self._lock:
            self._approvals[(task_id, step_id)] = approval

    def consume_approval(self, task_id: str, step_id: str) -> ApprovalRequest | None:
        with self._lock:
            return self._approvals.pop((task_id, step_id), None)


state_store = InMemoryStateStore()
