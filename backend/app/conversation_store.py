from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Literal

ActorType = Literal["visitor", "employee", "operator"]
Scene = Literal["reception", "office", "meeting"]
Language = Literal["zh", "en"]


@dataclass
class ConversationState:
    conversation_id: str
    language: Language = "zh"
    actor_type: ActorType = "visitor"
    current_scene: Scene = "reception"
    active_task_id: str | None = None
    last_visible_answer: str = ""
    last_command: str = ""


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
            return state

    def snapshot(self, conversation_id: str) -> dict | None:
        with self._lock:
            state = self._items.get(conversation_id)
            return asdict(state) if state else None


conversation_store = ConversationStore()
