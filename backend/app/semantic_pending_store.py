from __future__ import annotations

import time
from threading import RLock
from typing import Any

from app.semantic_route_models import PendingIntent


class SemanticPendingIntentStore:
    """Stores one short-lived conversational expectation per Visit.

    A pending intent is not inferred by scanning arbitrary history. It is written
    only when the assistant actually asks a conversion, confirmation or
    clarification question, and is consumed by at most the next user turn.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[tuple[str, str], PendingIntent] = {}
        self._maximum_age_seconds = 180.0

    @staticmethod
    def _key(conversation_id: str, visit_id: str) -> tuple[str, str]:
        conversation = str(conversation_id or "").strip()
        visit = str(visit_id or "").strip()
        if not conversation or not visit:
            raise ValueError("conversation_id and visit_id are required")
        return conversation, visit

    def configure(self, *, maximum_age_seconds: float) -> None:
        with self._lock:
            self._maximum_age_seconds = max(10.0, float(maximum_age_seconds))

    def set(
        self,
        conversation_id: str,
        visit_id: str,
        *,
        intent_type: str,
        source_turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        remaining_user_turns: int = 1,
    ) -> PendingIntent:
        key = self._key(conversation_id, visit_id)
        value = PendingIntent(
            conversation_id=key[0],
            visit_id=key[1],
            intent_type=intent_type,  # type: ignore[arg-type]
            source_turn_id=source_turn_id,
            created_at_epoch=time.time(),
            remaining_user_turns=max(1, min(3, int(remaining_user_turns))),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._values[key] = value
        return value.model_copy(deep=True)

    def peek(self, conversation_id: str, visit_id: str) -> PendingIntent | None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            value = self._values.get(key)
            if value is None:
                return None
            if time.time() - value.created_at_epoch > self._maximum_age_seconds:
                self._values.pop(key, None)
                return None
            return value.model_copy(deep=True)

    def consume(self, conversation_id: str, visit_id: str) -> PendingIntent | None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            value = self._values.get(key)
            if value is None:
                return None
            if time.time() - value.created_at_epoch > self._maximum_age_seconds:
                self._values.pop(key, None)
                return None
            value.remaining_user_turns -= 1
            result = value.model_copy(deep=True)
            if value.remaining_user_turns <= 0:
                self._values.pop(key, None)
            else:
                self._values[key] = value
            return result

    def clear(self, conversation_id: str, visit_id: str) -> None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            self._values.pop(key, None)

    def clear_visit(self, visit_id: str) -> int:
        clean = str(visit_id or "").strip()
        if not clean:
            return 0
        with self._lock:
            keys = [key for key in self._values if key[1] == clean]
            for key in keys:
                self._values.pop(key, None)
            return len(keys)

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            expired = [
                key
                for key, value in self._values.items()
                if now - value.created_at_epoch > self._maximum_age_seconds
            ]
            for key in expired:
                self._values.pop(key, None)
            return {
                "active_count": len(self._values),
                "maximum_age_seconds": self._maximum_age_seconds,
                "items": [
                    {
                        "conversation_id": value.conversation_id,
                        "visit_id": value.visit_id,
                        "intent_type": value.intent_type,
                        "remaining_user_turns": value.remaining_user_turns,
                        "age_seconds": round(now - value.created_at_epoch, 3),
                    }
                    for value in self._values.values()
                ],
            }


semantic_pending_intents = SemanticPendingIntentStore()
