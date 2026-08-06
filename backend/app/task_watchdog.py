from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime, timedelta

from app.state_store import state_store

logger = logging.getLogger(__name__)


def _approval_timeout_seconds() -> int:
    try:
        return max(15, int(os.getenv("SMART_OFFICE_APPROVAL_TIMEOUT_SECONDS", "120")))
    except ValueError:
        return 120


def _runtime_timeout_seconds() -> int:
    try:
        value = int(os.getenv("SMART_OFFICE_TASK_RUNTIME_TIMEOUT_SECONDS", "165"))
    except ValueError:
        value = 165
    return max(30, min(900, value))


class TaskWatchdog:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="smart-office-task-watchdog",
            daemon=True,
        )
        self._thread.start()

    def _cancel_task(self, task, reason: str, event_name: str) -> None:
        state_store.update_pending_steps(
            task.task_id,
            "cancelled",
            message=reason,
        )
        state_store.set_status(
            task.task_id,
            "cancelled",
            summary=reason,
        )
        logger.warning(
            event_name,
            extra={
                "task_id": task.task_id,
                "owner_conversation_id": task.owner_conversation_id,
                "owner_visit_id": task.owner_visit_id,
                "task_status": task.status,
                "updated_at": task.updated_at.isoformat(),
            },
        )

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            now = datetime.now(UTC)
            approval_timeout = timedelta(seconds=_approval_timeout_seconds())
            runtime_timeout = timedelta(seconds=_runtime_timeout_seconds())
            for task in state_store.list_tasks():
                if task.status in {"created", "planning", "running"}:
                    if now - task.updated_at < runtime_timeout:
                        continue
                    reason = (
                        f"Task made no observable progress for "
                        f"{_runtime_timeout_seconds()} seconds and was cancelled "
                        "to release the conversation runtime."
                    )
                    self._cancel_task(task, reason, "task_runtime_stalled")
                    continue

                if task.status != "waiting_approval":
                    continue
                deadline = task.approval_deadline_at
                if deadline is None:
                    deadline = task.updated_at + approval_timeout
                    state_store.set_approval_deadline(task.task_id, deadline)
                if now < deadline:
                    continue
                reason = (
                    f"Approval expired after {_approval_timeout_seconds()} seconds; "
                    "the task was cancelled to release the Visit."
                )
                self._cancel_task(task, reason, "task_approval_expired")

    def stop(self) -> None:
        self._stop.set()


# Process-wide service, not a behavioral monkey patch. Importing the Backend app
# starts one daemon watchdog for all task runtimes.
task_watchdog = TaskWatchdog()
