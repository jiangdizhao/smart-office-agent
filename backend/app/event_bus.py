from collections import defaultdict
from threading import RLock
from uuid import uuid4

from app.models import EventType, StepEvent, TaskSession
from app.state_store import state_store, utc_now
from app.task_logger import log_task_record


class InMemoryEventBus:
    def __init__(self) -> None:
        self._events: dict[str, list[StepEvent]] = defaultdict(list)
        self._lock = RLock()

    def publish(
        self,
        *,
        task_id: str,
        event_type: EventType,
        message: str,
        step_id: str | None = None,
        data: dict | None = None,
    ) -> StepEvent:
        event = StepEvent(
            event_id=str(uuid4()),
            task_id=task_id,
            step_id=step_id,
            type=event_type,
            message=message,
            data=data or {},
            timestamp=utc_now(),
        )

        with self._lock:
            self._events[task_id].append(event)

        state_store.append_event(task_id, event)
        log_task_record(task_id, "event", event.model_dump(mode="json"))
        return event

    def get_events(self, task_id: str) -> list[StepEvent]:
        with self._lock:
            return list(self._events.get(task_id, []))

    def build_demo_events(self, task: TaskSession) -> list[StepEvent]:
        first_step_id = task.steps[0].step_id if task.steps else None
        demo_sequence: list[tuple[EventType, str, str | None, dict]] = [
            (
                "planning",
                "Demo stream: planner has created a task graph.",
                None,
                {"step_count": len(task.steps), "execute": task.execute},
            ),
            (
                "step_started",
                "Demo stream: executor would start the first planned step.",
                first_step_id,
                {"fake": True},
            ),
            (
                "verification_result",
                "Demo stream: verifier would check the tool result.",
                first_step_id,
                {"fake": True, "ok": True},
            ),
            (
                "completed",
                "Demo stream: fake SSE event sequence finished.",
                None,
                {"fake": True},
            ),
        ]

        return [
            StepEvent(
                event_id=str(uuid4()),
                task_id=task.task_id,
                step_id=step_id,
                type=event_type,
                message=message,
                data=data,
                timestamp=utc_now(),
            )
            for event_type, message, step_id, data in demo_sequence
        ]


event_bus = InMemoryEventBus()
