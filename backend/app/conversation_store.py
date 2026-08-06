from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Literal

from app.state_store import state_store

ActorType = Literal["visitor", "employee", "operator"]
Scene = Literal["reception", "office", "meeting"]
Language = Literal["zh", "en"]
ConversationPhase = Literal["standby", "engaged", "awaiting_user", "task_active", "closing"]
MessageRole = Literal["user", "assistant", "system"]

_MAX_MESSAGES = 16
_MAX_REPLACED_VISIT_ARCHIVES = 128
_MAX_SUMMARY_POINTS = 6


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


def _summary_clean(text: str, maximum: int = 120) -> str:
    clean = " ".join(str(text or "").strip().split())
    clean = re.sub(
        r"^(?:啊|哦|嗯哼?|好嘞|好的|好[，,]|行[，,]|当然可以[，,]?|让我来[，,]?|"
        r"ah|oh|mm-hm|right|well|all right|certainly)[\s，,。.!-]*",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    return clean[:maximum].rstrip(" ，,。.!！?")


def _append_unique(points: list[str], value: str) -> None:
    clean = _summary_clean(value)
    if not clean:
        return
    fingerprint = re.sub(r"[\W_]+", "", clean).casefold()
    if not fingerprint:
        return
    for existing in points:
        existing_fingerprint = re.sub(r"[\W_]+", "", existing).casefold()
        if fingerprint == existing_fingerprint or fingerprint in existing_fingerprint or existing_fingerprint in fingerprint:
            return
    points.append(clean)


def _role_point(text: str, language: Language) -> str | None:
    if language == "zh":
        match = re.search(
            r"(?:我是|我做|我从事|我主要做|我主要负责|我负责)(?:一名|一个)?\s*([^，。！？]{2,36})",
            text,
        )
        if match:
            return f"职业/职责：{_summary_clean(match.group(1), 50)}"
        return None
    match = re.search(
        r"\b(?:I am|I'm|I work as|I mainly work as|I am responsible for)\s+(?:an?\s+)?([^,.!?]{2,48})",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"Role: {_summary_clean(match.group(1), 60)}"
    return None


def _key_point_summary(state: "ConversationState") -> str:
    points: list[str] = []
    language = state.language

    if state.display_name:
        _append_unique(
            points,
            f"访客：{state.display_name}" if language == "zh" else f"Visitor: {state.display_name}",
        )

    for message in state.messages:
        text = _summary_clean(message.text, 180)
        if not text:
            continue
        lower = text.casefold()

        if message.role == "user":
            role = _role_point(text, language)
            if role:
                _append_unique(points, role)
                continue

            if re.search(r"价格|报价|多少钱|费用|成本|price|pricing|quote|cost|how much", text, re.IGNORECASE):
                _append_unique(points, "关注价格与方案范围" if language == "zh" else "Asked about price and solution scope")
                continue
            if re.search(r"隐私|个人信息|人脸|录音|数据|privacy|personal information|face data|recording", text, re.IGNORECASE):
                _append_unique(points, "关注隐私与数据处理" if language == "zh" else "Asked about privacy and data handling")
                continue
            if re.search(r"联系方式|登记|联系我|可以联系|contact|registration|follow up", text, re.IGNORECASE):
                _append_unique(points, "讨论了后续联系或信息登记" if language == "zh" else "Discussed follow-up contact or registration")
                continue
            if re.search(r"预约|会议时间|book|booking|appointment", text, re.IGNORECASE):
                _append_unique(points, "讨论了会议预约" if language == "zh" else "Discussed meeting booking")
                continue
            if re.search(
                r"需要|想要|希望|关注|痛点|问题|客户跟进|邮件|会议|重复|自动化|"
                r"need|want|would like|interested|pain point|follow-up|email|meeting|automation",
                text,
                re.IGNORECASE,
            ):
                label = "需求：" if language == "zh" else "Need: "
                _append_unique(points, f"{label}{text}")
                continue

        if message.role == "assistant":
            is_operational = bool(
                message.task_id
                or (message.route and "office" in message.route.casefold())
                or re.search(
                    r"已(?:经)?(?:打开|播放|开始|关闭|调整|设置|生成|创建|发送|跳转|翻到)|"
                    r"演示已经开始|PowerPoint.*(?:打开|开始)|"
                    r"(?:opened|started|closed|adjusted|set|created|sent|moved to slide|verified)",
                    text,
                    re.IGNORECASE,
                )
            )
            if is_operational:
                label = "已演示：" if language == "zh" else "Demonstrated: "
                _append_unique(points, f"{label}{text}")
                continue
            if re.search(r"打开登记信息表|打开登记|visitor registration|registration form", text, re.IGNORECASE):
                _append_unique(points, "已打开联系方式登记表" if language == "zh" else "Opened contact registration")
                continue
            if re.search(r"打开.*预约|预约日历|booking calendar", text, re.IGNORECASE):
                _append_unique(points, "已打开会议预约" if language == "zh" else "Opened meeting booking")
                continue

        if len(points) >= _MAX_SUMMARY_POINTS:
            break

    if not points:
        return "暂无有效要点" if language == "zh" else "No substantive points yet"
    return "\n".join(f"• {point}" for point in points[:_MAX_SUMMARY_POINTS])


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
    revision: int = 0


class ConversationStore:
    def __init__(self) -> None:
        self._items: dict[str, ConversationState] = {}
        self._replaced_archives: dict[tuple[str, str], dict[str, Any]] = {}
        self._archive_order: list[tuple[str, str]] = []
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

    def current_visit_id(self, conversation_id: str) -> str | None:
        with self._lock:
            state = self._items.get(conversation_id)
            return None if state is None else state.visit_id

    def is_current_visit(self, conversation_id: str, visit_id: str | None) -> bool:
        clean = str(visit_id or "").strip()
        with self._lock:
            state = self._items.get(conversation_id)
            if state is None:
                return not clean
            if not clean:
                return state.visit_id is None
            return state.visit_id == clean

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
        expected_visit_id: str | None = None,
    ) -> ConversationState:
        with self._lock:
            state = self._items[conversation_id]
            self._require_current_visit_locked(state, expected_visit_id)
            if current_scene is not None:
                state.current_scene = current_scene
            if set_active_task:
                state.active_task_id = active_task_id
                if active_task_id:
                    state_store.bind_task_owner(
                        active_task_id,
                        conversation_id=state.conversation_id,
                        visit_id=state.visit_id,
                        actor_type=state.actor_type,
                    )
            if last_visible_answer is not None:
                state.last_visible_answer = last_visible_answer
            if last_command is not None:
                state.last_command = last_command
            if conversation_phase is not None:
                state.conversation_phase = conversation_phase
                state.awaiting_user_since = _now() if conversation_phase == "awaiting_user" else None
            state.last_activity_at = _now()
            state.revision += 1
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
        expected_visit_id: str | None = None,
    ) -> ConversationState:
        clean = " ".join(text.strip().split())
        with self._lock:
            state = self.get_or_create(
                conversation_id,
                language=language,
                actor_type=actor_type,
            )
            self._require_current_visit_locked(state, expected_visit_id)
            state.conversation_phase = "engaged"
            state.awaiting_user_since = None
            state.last_command = clean
            state.last_activity_at = _now()
            state.revision += 1
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
        expected_visit_id: str | None = None,
    ) -> ConversationState:
        clean = " ".join(text.strip().split())
        with self._lock:
            state = self._items[conversation_id]
            self._require_current_visit_locked(state, expected_visit_id)
            now = _now()
            state.last_visible_answer = clean
            state.last_activity_at = now
            state.active_task_id = task_id if task_id else state.active_task_id
            if task_id:
                state_store.bind_task_owner(
                    task_id,
                    conversation_id=state.conversation_id,
                    visit_id=state.visit_id,
                    actor_type=state.actor_type,
                )
            state.revision += 1
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
        expected_visit_id: str | None = None,
    ) -> ConversationState:
        with self._lock:
            state = self._items[conversation_id]
            self._require_current_visit_locked(state, expected_visit_id)
            now = _now()
            state.last_activity_at = now
            state.revision += 1
            if active:
                state.active_task_id = task_id
                state.conversation_phase = "task_active"
                state.awaiting_user_since = None
                if task_id:
                    state_store.bind_task_owner(
                        task_id,
                        conversation_id=state.conversation_id,
                        visit_id=state.visit_id,
                        actor_type=state.actor_type,
                    )
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
                state.revision += 1
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
                if state.visit_id:
                    self._store_replaced_archive_locked(state)
                self._reset_for_new_visit_locked(state)
                state.visit_id = visit_id
                state.revision += 1
            self._refresh_idle_locked(state)
            if state.last_greeted_visit_id == visit_id:
                return False, "", "visit_already_greeted", state
            if state.conversation_phase != "standby":
                return False, "", f"conversation_phase={state.conversation_phase}", state
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
            state.revision += 1
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
        clean_visit = str(visit_id or "").strip()
        with self._lock:
            state = self._items.get(conversation_id)
            if clean_visit:
                cached = self._replaced_archives.pop((conversation_id, clean_visit), None)
                if cached is not None:
                    try:
                        self._archive_order.remove((conversation_id, clean_visit))
                    except ValueError:
                        pass
                    return cached
            if state is None:
                return {
                    "conversation_id": conversation_id,
                    "visit_id": clean_visit or None,
                    "identity_id": None,
                    "display_name": None,
                    "conversation_summary": "",
                    "recent_messages": [],
                    "active_task_id": None,
                    "ended": False,
                    "reason": "conversation_not_found",
                }
            if clean_visit and state.visit_id and clean_visit != state.visit_id:
                return {
                    "conversation_id": conversation_id,
                    "visit_id": clean_visit,
                    "identity_id": None,
                    "display_name": None,
                    "conversation_summary": "",
                    "recent_messages": [],
                    "active_task_id": None,
                    "ended": False,
                    "reason": "visit_id_mismatch",
                }
            archive = self._archive_state_locked(state)
            self._reset_for_new_visit_locked(state)
            state.revision += 1
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
                "revision": state.revision,
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
        state.conversation_summary = _key_point_summary(state)

    def _archive_state_locked(self, state: ConversationState) -> dict[str, Any]:
        return {
            "conversation_id": state.conversation_id,
            "visit_id": state.visit_id,
            "identity_id": state.identity_id,
            "display_name": state.display_name,
            "conversation_summary": state.conversation_summary,
            "recent_messages": [asdict(message) for message in state.messages[-8:]],
            "active_task_id": state.active_task_id,
            "actor_type": state.actor_type,
            "ended": True,
            "ended_at": _now(),
        }

    def _store_replaced_archive_locked(self, state: ConversationState) -> None:
        if not state.visit_id:
            return
        key = (state.conversation_id, state.visit_id)
        self._replaced_archives[key] = self._archive_state_locked(state)
        if key in self._archive_order:
            self._archive_order.remove(key)
        self._archive_order.append(key)
        while len(self._archive_order) > _MAX_REPLACED_VISIT_ARCHIVES:
            oldest = self._archive_order.pop(0)
            self._replaced_archives.pop(oldest, None)

    def _require_current_visit_locked(
        self,
        state: ConversationState,
        expected_visit_id: str | None,
    ) -> None:
        expected = str(expected_visit_id or "").strip()
        if not expected:
            return
        if state.visit_id != expected:
            raise RuntimeError(
                f"stale_visit: expected={expected} current={state.visit_id or 'none'}"
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
            state.revision += 1


conversation_store = ConversationStore()
