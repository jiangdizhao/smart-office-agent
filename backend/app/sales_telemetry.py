from __future__ import annotations

from collections import deque
from threading import RLock
from typing import Any
from uuid import uuid4

from app.sales_models import SalesStage, SalesTelemetryEvent
from app.sales_policy import sales_runtime_policy


class SalesTelemetryStore:
    """Bounded in-memory Phase 0 telemetry contract.

    No conversation text, contact details or inferred sensitive attributes are stored.
    Phase 1 will emit lifecycle timings and counters through this interface.
    """

    def __init__(self, maximum_events: int = 1_000) -> None:
        self.maximum_events = max(100, int(maximum_events))
        self._lock = RLock()
        self._events: deque[SalesTelemetryEvent] = deque(maxlen=self.maximum_events)

    def emit(
        self,
        event_type: SalesTelemetryEvent.model_fields["event_type"].annotation,  # type: ignore[name-defined]
        *,
        conversation_id: str,
        visit_id: str,
        stage: SalesStage | None = None,
        data: dict[str, Any] | None = None,
    ) -> SalesTelemetryEvent | None:
        if not sales_runtime_policy.feature_flags().telemetry_enabled:
            return None
        safe_data = dict(data or {})
        for prohibited_key in (
            "transcript",
            "email",
            "phone",
            "address",
            "password",
            "face_embedding",
            "image",
        ):
            safe_data.pop(prohibited_key, None)
        event = SalesTelemetryEvent(
            event_id=f"sales-event-{uuid4().hex}",
            event_type=event_type,
            conversation_id=conversation_id,
            visit_id=visit_id,
            stage=stage,
            data=safe_data,
        )
        with self._lock:
            self._events.append(event)
        return SalesTelemetryEvent.model_validate(event.model_dump(mode="python"))

    def events_for_visit(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> list[SalesTelemetryEvent]:
        with self._lock:
            return [
                SalesTelemetryEvent.model_validate(item.model_dump(mode="python"))
                for item in self._events
                if item.conversation_id == conversation_id and item.visit_id == visit_id
            ]

    def status(self) -> dict[str, int | bool | str]:
        with self._lock:
            return {
                "schema_version": "sales-telemetry-v1",
                "enabled": sales_runtime_policy.feature_flags().telemetry_enabled,
                "event_count": len(self._events),
                "maximum_events": self.maximum_events,
                "storage": "bounded_memory_only",
                "stores_transcript": False,
                "stores_contact_details": False,
            }

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


sales_telemetry = SalesTelemetryStore()
