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


def _visitor_greeting(language: Language, detection: dict[str, Any]) -> str:
    kind = str(detection.get("greeting_kind") or "new_anonymous").strip().casefold()
    display_name = " ".join(str(detection.get("display_name") or "").strip().split())[:80]
    returning = bool(detection.get("returning_visitor"))

    if kind == "registered_identity" and display_name:
        return f"欢迎回来，{display_name}。" if language == "zh" else f"Welcome back, {display_name}."
    if kind == "returning_anonymous" or returning:
        return "欢迎回来。" if language == "zh" else "Welcome back."
    return "欢迎来到我们的办公室。" if language == "zh" else "Welcome to our office."


def _visit_id(detection: dict[str, Any], conversation_id: str) -> str:
    value = str(
        detection.get("visitor_session_id")
        or detection.get("visit_id")
        or detection.get("provisional_session_id")
        or ""
    ).strip()
    return value[:160] or f"local-{conversation_id}"


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
    last_proximity_detection: dict[str, Any] | None = None
    visit_id: str | None = None
    identity_id: str | None = None
    display_name: str | None = None
    registered_memory_summary: str = ""
    last_greeted_visit_id: str | None = None


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
        registered_memory: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str, ConversationState]:
        with self._lock:
            state = self.get_or_create(
                conversation_id,
                language=language,
                actor_type=actor_type,
            )
            visit_id = _visit_id(detection, conversation_id)
            if state.visit_id != visit_id:
                if state.active_task_id is not None:
                    return False, "", "active_task", state
                self._reset_for_new_visit_locked(state)
                state.visit_id = visit_id
            self._refresh_idle_locked(state)
            if state.last_greeted_visit_id == visit_id:
                return False, "", "visit_already_greeted", state
            if state.conversation_phase != "standby":
                return False, "", f"conversation_phase={state.conversation_phase}", state
            if state.active_task_id is not None:
                return False, "", "active_task", state

            identity_id = str(detection.get("identity_id") or "").strip() or None
            display_name = " ".join(str(detection.get("display_name") or "").strip().split())[:80] or None
            state.identity_id = identity_id
            state.display_name = display_name
            state.registered_memory_summary = (
                " ".join(str((registered_memory or {}).get("memory_summary") or "").strip().split())[:8_000]
                if identity_id
                else ""
            )

            greeting = _visitor_greeting(language, detection)
            now = _now()
            state.conversation_phase = "awaiting_user"
            state.awaiting_user_since = now
            state.last_activity_at = now
            state.last_visible_answer = greeting
            state.last_proximity_detection = dict(detection)
            state.last_greeted_visit_id = visit_id
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

    def end_visit(self, conversation_id: str, *, visit_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            state = self._items.get(conversation_id)
            if state is None:
                return {
                    "conversation_id": conversation_id,
                    "visit_id": visit_id,
                    "identity_id": None,
                    "display_name": None,
                    "conversation_summary": "",
                    "recent_messages": [],
                    "ended": False,
                }
            if visit_id and state.visit_id and visit_id != state.visit_id:
                return {
                    "conversation_id": conversation_id,
                    "visit_id": visit_id,
                    "identity_id": state.identity_id,
                    "display_name": state.display_name,
                    "conversation_summary": state.conversation_summary,
                    "recent_messages": [asdict(message) for message in state.messages[-8:]],
                    "ended": False,
                    "reason": "visit_id_mismatch",
                }
            archive = {
                "conversation_id": state.conversation_id,
                "visit_id": state.visit_id,
                "identity_id": state.identity_id,
                "display_name": state.display_name,
                "conversation_summary": state.conversation_summary,
                "recent_messages": [asdict(message) for message in state.messages[-8:]],
                "ended": True,
            }
            self._reset_for_new_visit_locked(state)
            return archive

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
                "registered_memory_summary": state.registered_memory_summary,
                "visit_id": state.visit_id,
                "identity_id": state.identity_id,
                "display_name": state.display_name,
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

    def _reset_for_new_visit_locked(self, state: ConversationState) -> None:
        state.current_scene = "reception"
        state.conversation_phase = "standby"
        state.active_task_id = None
        state.last_visible_answer = ""
        state.last_command = ""
        state.messages.clear()
        state.conversation_summary = ""
        state.last_activity_at = _now()
        state.awaiting_user_since = None
        state.last_proximity_detection = None
        state.visit_id = None
        state.identity_id = None
        state.display_name = None
        state.registered_memory_summary = ""
        state.last_greeted_visit_id = None

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
