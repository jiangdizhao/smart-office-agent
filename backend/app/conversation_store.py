from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Literal

ActorType = Literal["visitor", "employee", "operator"]
Scene = Literal["reception", "office", "meeting"]
Language = Literal["zh", "en"]
ConversationPhase = Literal["standby", "engaged", "awaiting_user", "task_active", "closing"]
MessageRole = Literal["user", "assistant", "system"]

_MAX_MESSAGES = 16


def _now() -> datetime:
    return datetime.now(UTC)


def _idle_timeout_seconds() -> int:
    try:
        return max(30, int(os.getenv("SMART_OFFICE_CONVERSATION_IDLE_SECONDS", "180")))
    except ValueError:
        return 180


def _greeting_cooldown_seconds() -> int:
    try:
        return max(10, int(os.getenv("SMART_OFFICE_PROXIMITY_GREETING_COOLDOWN_SECONDS", "30")))
    except ValueError:
        return 30


@dataclass
class ConversationMessage:
    role: MessageRole
    text: str
    timestamp: datetime = field(default_factory=_now)
    source: str = ""
    route: str | None = None
    task_id: str | None = None


@dataclass
class ConversationState:
    conversation_id: str
    language: Language = "zh"
    actor_type: ActorType = "visitor"
    current_scene: Scene = "reception"
    conversation_phase: ConversationPhase = "standby"
    active_task_id: str | None = None
    last_visible_answer: str = ""
    last_command: str = ""
    messages: list[ConversationMessage] = field(default_factory=list)
    conversation_summary: str = ""
    created_at: datetime = field(default_factory=_now)
    last_activity_at: datetime = field(default_factory=_now)
    awaiting_user_since: datetime | None = None
    last_proximity_greeting_at: datetime | None = None
    last_proximity_detection: dict[str, Any] | None = None


class ConversationStore:
    def __init__(self) -> None:
        self._items: dict[str, ConversationState] = {}
        self._lock = RLock()

    def get_or_create(
        self,
        conversation_id: str,
        *,
        language: Language,
        actor_type: ActorType,
    ) -> ConversationState:
        with self._lock:
            state = self._items.get(conversation_id)
            if state is None:
                state = ConversationState(
                    conversation_id=conversation_id,
                    language=language,
                    actor_type=actor_type,
                )
                self._items[conversation_id] = state
            else:
                state.language = language
                state.actor_type = actor_type
            self._refresh_idle_locked(state)
            return state

    def update(
        self,
        conversation_id: str,
        *,
        current_scene: Scene | None = None,
        active_task_id: str | None = None,
        set_active_task: bool = False,
        last_visible_answer: str | None = None,
        last_command: str | None = None,
        conversation_phase: ConversationPhase | None = None,
    ) -> ConversationState:
        with self._lock:
            state = self._items[conversation_id]
            if current_scene is not None:
                state.current_scene = current_scene
            if set_active_task:
                state.active_task_id = active_task_id
            if last_visible_answer is not None:
                state.last_visible_answer = last_visible_answer
            if last_command is not None:
                state.last_command = last_command
            if conversation_phase is not None:
                state.conversation_phase = conversation_phase
                state.awaiting_user_since = _now() if conversation_phase == "awaiting_user" else None
            state.last_activity_at = _now()
            self._refresh_summary_locked(state)
            return state

    def begin_user_turn(
        self,
        conversation_id: str,
        *,
        language: Language,
        actor_type: ActorType,
        text: str,
        source: str,
    ) -> ConversationState:
        clean = " ".join(text.strip().split())
        with self._lock:
            state = self.get_or_create(
                conversation_id,
                language=language,
                actor_type=actor_type,
            )
            state.conversation_phase = "engaged"
            state.awaiting_user_since = None
            state.last_command = clean
            state.last_activity_at = _now()
            if clean:
                self._append_message_locked(
                    state,
                    ConversationMessage(role="user", text=clean, source=source),
                )
            self._refresh_summary_locked(state)
            return state

    def complete_assistant_turn(
        self,
        conversation_id: str,
        *,
        text: str,
        route: str | None = None,
        task_id: str | None = None,
        expect_reply: bool = True,
        source: str = "agent",
    ) -> ConversationState:
        clean = " ".join(text.strip().split())
        with self._lock:
            state = self._items[conversation_id]
            now = _now()
            state.last_visible_answer = clean
            state.last_activity_at = now
            state.active_task_id = task_id if task_id else state.active_task_id
            if clean:
                self._append_message_locked(
                    state,
                    ConversationMessage(
                        role="assistant",
                        text=clean,
                        source=source,
                        route=route,
                        task_id=task_id,
                    ),
                )
            if task_id:
                state.conversation_phase = "task_active"
                state.awaiting_user_since = None
            elif expect_reply:
                state.conversation_phase = "awaiting_user"
                state.awaiting_user_since = now
            else:
                state.conversation_phase = "engaged"
                state.awaiting_user_since = None
            self._refresh_summary_locked(state)
            return state

    def set_task_state(
        self,
        conversation_id: str,
        *,
        task_id: str | None,
        active: bool,
        final_text: str = "",
    ) -> ConversationState:
        with self._lock:
            state = self._items[conversation_id]
            now = _now()
            state.last_activity_at = now
            if active:
                state.active_task_id = task_id
                state.conversation_phase = "task_active"
                state.awaiting_user_since = None
            else:
                if task_id is None or state.active_task_id == task_id:
                    state.active_task_id = None
                state.conversation_phase = "awaiting_user"
                state.awaiting_user_since = now
                if final_text.strip():
                    state.last_visible_answer = " ".join(final_text.strip().split())
            self._refresh_summary_locked(state)
            return state

    def mark_standby(self, conversation_id: str) -> ConversationState:
        with self._lock:
            state = self._items[conversation_id]
            if state.active_task_id is None:
                state.conversation_phase = "standby"
                state.awaiting_user_since = None
            return state

    def proximity_greeting(
        self,
        conversation_id: str,
        *,
        language: Language,
        actor_type: ActorType,
        detection: dict[str, Any],
    ) -> tuple[bool, str, str, ConversationState]:
        with self._lock:
            state = self.get_or_create(
                conversation_id,
                language=language,
                actor_type=actor_type,
            )
            self._refresh_idle_locked(state)
            if state.conversation_phase != "standby":
                return False, "", f"conversation_phase={state.conversation_phase}", state
            if state.active_task_id is not None:
                return False, "", "active_task", state
            now = _now()
            if (
                state.last_proximity_greeting_at is not None
                and now - state.last_proximity_greeting_at
                < timedelta(seconds=_greeting_cooldown_seconds())
            ):
                return False, "", "cooldown", state

            greeting = "Welcome to our office."
            state.conversation_phase = "awaiting_user"
            state.awaiting_user_since = now
            state.last_activity_at = now
            state.last_visible_answer = greeting
            state.last_proximity_greeting_at = now
            state.last_proximity_detection = dict(detection)
            self._append_message_locked(
                state,
                ConversationMessage(
                    role="assistant",
                    text=greeting,
                    source="camera_proximity",
                    route="proximity_greeting",
                ),
            )
            self._refresh_summary_locked(state)
            return True, greeting, "triggered", state

    def snapshot(
        self,
        conversation_id: str,
        *,
        language: Language | None = None,
        actor_type: ActorType | None = None,
    ) -> dict | None:
        with self._lock:
            state = self._items.get(conversation_id)
            if state is None:
                if language is None or actor_type is None:
                    return None
                state = ConversationState(
                    conversation_id=conversation_id,
                    language=language,
                    actor_type=actor_type,
                )
                self._items[conversation_id] = state
            if language is not None:
                state.language = language
            if actor_type is not None:
                state.actor_type = actor_type
            self._refresh_idle_locked(state)
            return asdict(state)

    def context_snapshot(
        self,
        conversation_id: str,
        *,
        language: Language,
        actor_type: ActorType,
    ) -> dict:
        with self._lock:
            state = self.get_or_create(
                conversation_id,
                language=language,
                actor_type=actor_type,
            )
            return {
                "conversation_id": state.conversation_id,
                "language": state.language,
                "actor_type": state.actor_type,
                "current_scene": state.current_scene,
                "conversation_phase": state.conversation_phase,
                "active_task_id": state.active_task_id,
                "last_visible_answer": state.last_visible_answer,
                "last_command": state.last_command,
                "conversation_summary": state.conversation_summary,
                "recent_messages": [asdict(message) for message in state.messages[-_MAX_MESSAGES:]],
                "last_activity_at": state.last_activity_at,
                "awaiting_user_since": state.awaiting_user_since,
                "idle_timeout_seconds": _idle_timeout_seconds(),
            }

    def _append_message_locked(
        self,
        state: ConversationState,
        message: ConversationMessage,
    ) -> None:
        state.messages.append(message)
        if len(state.messages) > _MAX_MESSAGES:
            del state.messages[:-_MAX_MESSAGES]

    def _refresh_summary_locked(self, state: ConversationState) -> None:
        recent = state.messages[-6:]
        state.conversation_summary = " | ".join(
            f"{message.role}: {message.text[:180]}" for message in recent
        )

    def _refresh_idle_locked(self, state: ConversationState) -> None:
        if state.active_task_id is not None:
            state.conversation_phase = "task_active"
            return
        if state.conversation_phase not in {"engaged", "awaiting_user", "closing"}:
            return
        if _now() - state.last_activity_at >= timedelta(seconds=_idle_timeout_seconds()):
            state.conversation_phase = "standby"
            state.awaiting_user_since = None


conversation_store = ConversationStore()
