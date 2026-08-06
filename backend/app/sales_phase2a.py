from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.sales_models import Language, SalesSessionState
from app.sales_policy import sales_runtime_policy
from app.sales_session_store import sales_session_store

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "sales_phase2a.json"
_SALES_REPLY_MODES = {"opening", "pure_sales", "hybrid_sales", "proactive_sales"}
_PHASE2A_FIRST = "sales_phase2a_proactive_first"
_PHASE2A_SECOND = "sales_phase2a_proactive_second"


class Phase2AVoiceDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["voice-delivery-v1"] = "voice-delivery-v1"
    style: Literal[
        "warm_confident",
        "curious_discovery",
        "confident_recommendation",
        "light_playful",
        "calm_reassuring",
        "verified_success",
        "neutral_exact",
    ] = "confident_recommendation"
    pace: Literal["measured", "natural", "natural_brisk"] = "natural"
    energy: Literal["low", "medium", "medium_high"] = "medium"
    question_tone: Literal["none", "curious", "inviting"] = "none"
    emphasis_terms: list[str] = Field(default_factory=list, max_length=6)
    humour_delivery: Literal["none", "light_smile"] = "none"
    pause_before_question: bool = False


class Phase2AProactiveReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sales-phase2a-reply-v1"] = "sales-phase2a-reply-v1"
    conversation_id: str
    visit_id: str
    language: Language
    reply_mode: Literal["proactive_sales"] = "proactive_sales"
    purpose: Literal[
        "sales_phase2a_proactive_first",
        "sales_phase2a_proactive_second",
    ]
    text: str
    fallback_text: str
    expect_user_response: bool
    question_field: str | None = None
    humour_theme: None = None
    delivery: Phase2AVoiceDelivery


class Phase2AProactiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    language: Language = "zh"
    user_speaking: bool = False
    agent_speaking: bool = False
    tool_active: bool = False
    interaction_input_active: bool = False


class Phase2AOutputResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    result: Literal["completed", "interrupted", "failed", "cancelled"]
    purpose: str = Field(default="", max_length=160)
    reply_mode: str = Field(default="", max_length=80)
    expect_user_response: bool = False
    question_field: str | None = Field(default=None, max_length=160)
    text: str = Field(default="", max_length=12_000)
    cancel_reason: str | None = Field(default=None, max_length=200)


class Phase2AProactiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    phase: Literal["phase2a_sales_continuity"] = "phase2a_sales_continuity"
    speak: bool
    reason: str
    reply: Phase2AProactiveReply | None = None
    session: dict[str, Any]
    continuity: dict[str, Any]


class _ContinuityState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    visit_id: str
    episode_id: str | None = None
    episode_source_purpose: str | None = None
    episode_source_reply_mode: str | None = None
    episode_nudge_count: int = 0
    total_nudge_count: int = 0
    active: bool = False
    last_output_result: str | None = None
    last_cancel_reason: str | None = None
    last_planned_action: str | None = None
    last_source_text_hash: str | None = None


class SalesPhase2AService:
    """Visit-fenced Phase 2A sales continuity and deterministic identity source.

    Phase 1 owns opening generation and profile extraction. This service owns what
    happens after a completed customer-facing sales output: it opens a fresh two-step
    continuity episode, preserves a Visit-wide budget, and chooses context-aware
    value reinforcement or a conversion invitation without exposing implementation
    technology.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[tuple[str, str], _ContinuityState] = {}
        self._config_cache: dict[str, Any] | None = None

    @staticmethod
    def _key(conversation_id: str, visit_id: str) -> tuple[str, str]:
        conversation = str(conversation_id or "").strip()
        visit = str(visit_id or "").strip()
        if not conversation or not visit:
            raise ValueError("conversation_id and visit_id are required")
        return conversation, visit

    def config(self) -> dict[str, Any]:
        with self._lock:
            if self._config_cache is None:
                try:
                    payload = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"Phase 2A sales configuration is invalid: {exc}") from exc
                if payload.get("schema_version") != "sales-phase2a-v1":
                    raise RuntimeError("Phase 2A sales configuration schema is unsupported.")
                self._config_cache = payload
            return dict(self._config_cache)

    def _state(self, conversation_id: str, visit_id: str) -> _ContinuityState:
        key = self._key(conversation_id, visit_id)
        state = self._states.get(key)
        if state is None:
            state = _ContinuityState(conversation_id=key[0], visit_id=key[1])
            self._states[key] = state
        return state

    @staticmethod
    def _text_hash(text: str) -> str | None:
        clean = " ".join(str(text or "").split())
        return hashlib.sha256(clean.encode("utf-8")).hexdigest() if clean else None

    @staticmethod
    def _is_sales_output(purpose: str, reply_mode: str) -> bool:
        purpose_clean = purpose.strip().casefold()
        mode_clean = reply_mode.strip().casefold()
        return purpose_clean.startswith("sales_") or mode_clean in _SALES_REPLY_MODES

    def self_introduction(self, language: Language) -> str:
        config = self.config()
        text = str(config["self_introduction"][language]).strip()
        if not text:
            raise RuntimeError("Phase 2A self-introduction text is empty.")
        return text

    def record_output(self, request: Phase2AOutputResultRequest) -> _ContinuityState:
        key = self._key(request.conversation_id, request.visit_id)
        session = sales_session_store.snapshot(*key)
        with self._lock:
            state = self._state(*key)
            state.last_output_result = request.result
            state.last_cancel_reason = request.cancel_reason

            if request.result != "completed":
                state.active = False
                self._states[key] = state
                return state.model_copy(deep=True)

            purpose = request.purpose.strip()
            reply_mode = request.reply_mode.strip()
            if not self._is_sales_output(purpose, reply_mode):
                self._states[key] = state
                return state.model_copy(deep=True)

            if reply_mode == "closing" or (session and (session.stage == "close" or session.disengaged)):
                state.active = False
                self._states[key] = state
                return state.model_copy(deep=True)

            if purpose == _PHASE2A_SECOND:
                state.active = False
                state.last_planned_action = "episode_completed"
                self._states[key] = state
                return state.model_copy(deep=True)

            if purpose == _PHASE2A_FIRST:
                state.active = True
                self._states[key] = state
                return state.model_copy(deep=True)

            state.episode_id = uuid4().hex
            state.episode_source_purpose = purpose or "sales_output"
            state.episode_source_reply_mode = reply_mode or "pure_sales"
            state.episode_nudge_count = 0
            state.active = True
            state.last_planned_action = "episode_armed"
            state.last_source_text_hash = self._text_hash(request.text)
            self._states[key] = state
            return state.model_copy(deep=True)

    @staticmethod
    def _session_payload(session: SalesSessionState | None) -> dict[str, Any]:
        return {} if session is None else session.model_dump(mode="json")

    def _continuity_payload(self, state: _ContinuityState) -> dict[str, Any]:
        config = self.config()
        return {
            **state.model_dump(mode="json"),
            "first_nudge_seconds": int(config["timing"]["first_nudge_seconds"]),
            "second_nudge_seconds": int(config["timing"]["second_nudge_seconds"]),
            "maximum_nudges_per_episode": int(config["limits"]["maximum_nudges_per_episode"]),
            "maximum_nudges_per_visit": int(config["limits"]["maximum_nudges_per_visit"]),
        }

    @staticmethod
    def _short_context(session: SalesSessionState, language: Language) -> str:
        role = str(session.explicit_facts.get("role") or "").strip()
        industry = str(session.explicit_facts.get("industry") or "").strip()
        if language == "en":
            if role and industry:
                return f"your {role} work in {industry}"
            if role:
                return f"your work as {role}"
            if industry:
                return f"your work in {industry}"
            return "the workflow you described"
        if role and industry:
            return f"您在{industry}领域负责{role}的工作"
        if role:
            return f"您负责{role}的工作"
        if industry:
            return f"您在{industry}领域的工作"
        return "您刚才提到的办公场景"

    def _adaptive_claim(self, session: SalesSessionState, language: Language) -> str:
        config = self.config()
        group = "consented" if session.profile_persisted else "anonymous"
        values = [str(item).strip() for item in config["adaptive_claims"][group][language] if str(item).strip()]
        if not values:
            values = [str(item).strip() for item in config["adaptive_claims"]["anonymous"][language] if str(item).strip()]
        seed = f"{session.conversation_id}|{session.visit_id}|{session.turn_count}|{session.value_delivered}"
        index = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:4], "big") % len(values)
        return values[index]

    def _first_nudge(self, session: SalesSessionState, language: Language) -> tuple[str, bool, str | None, str]:
        if session.pain_points:
            pain = session.pain_points[0]
            text = (
                f"结合您刚才提到的“{pain}”，最值得先看的不是泛泛的聊天，而是把这个环节接到可执行、可验证的办公流程里。"
                if language == "zh"
                else f"Based on the issue you mentioned — {pain} — the useful next step is not generic chat, but connecting that part of the work to a controlled, verifiable office workflow."
            )
            return text, False, None, "contextual_value_reinforcement"

        if session.interested_capabilities:
            capability = session.interested_capabilities[0].replace("_", " ")
            text = (
                f"您刚才关注的“{capability}”可以继续往真实流程里收窄，我可以先说明它怎样减少重复操作，再决定是否值得完整演示。"
                if language == "zh"
                else f"The {capability} capability you mentioned can be narrowed into a real workflow. I can first explain where it removes repetitive work, and then you can decide whether a complete demonstration is worthwhile."
            )
            return text, False, None, "capability_value_reinforcement"

        if session.explicit_facts:
            context = self._short_context(session, language)
            question = (
                "您更想先减少会议后的整理、客户跟进，还是重复行政工作？"
                if language == "zh"
                else "Would you rather reduce post-meeting work, customer follow-up or repetitive administration first?"
            )
            text = (
                f"从{context}来看，我可以继续把建议收窄到真正占用时间的环节。{question}"
                if language == "zh"
                else f"From {context}, I can narrow the recommendation to the part that actually consumes time. {question}"
            )
            return text, True, "office_pain_points", "role_based_discovery"

        question = (
            "您不必介绍很多信息，只要告诉我更关心会议、客户跟进，还是重复行政工作就可以。"
            if language == "zh"
            else "You do not need to provide a long introduction; just tell me whether meetings, customer follow-up or repetitive administration matters most."
        )
        return question, True, "office_pain_points", "low_pressure_discovery"

    def _second_nudge(self, session: SalesSessionState, language: Language) -> tuple[str, bool, str | None, str]:
        high_interest = bool(session.pain_points or session.interested_capabilities)
        if session.value_delivered and high_interest and sales_runtime_policy.can_offer_booking(session):
            offered, updated = sales_session_store.offer_booking(session.conversation_id, session.visit_id)
            if offered:
                context = self._short_context(updated, language)
                text = (
                    f"我可以围绕{context}安排一次完整体验。需要我现在打开预约日历吗？"
                    if language == "zh"
                    else f"I can arrange a complete experience around {context}. Shall I open the booking calendar now?"
                )
                return text, True, "booking", "contextual_booking_offer"

        claim = self._adaptive_claim(session, language)
        return claim, False, None, "adaptive_self_promotion"

    def plan_proactive(self, request: Phase2AProactiveRequest) -> Phase2AProactiveResponse:
        key = self._key(request.conversation_id, request.visit_id)
        session = sales_session_store.snapshot(*key)
        if session is None:
            return Phase2AProactiveResponse(
                speak=False,
                reason="sales_session_missing",
                session={},
                continuity=self._continuity_payload(_ContinuityState(conversation_id=key[0], visit_id=key[1])),
            )

        flags = sales_runtime_policy.feature_flags()
        if not flags.agent_enabled or not flags.proactive_enabled:
            with self._lock:
                state = self._state(*key).model_copy(deep=True)
            return Phase2AProactiveResponse(
                speak=False,
                reason="phase2a_proactive_disabled",
                session=self._session_payload(session),
                continuity=self._continuity_payload(state),
            )

        if request.user_speaking or request.agent_speaking or request.tool_active or request.interaction_input_active:
            with self._lock:
                state = self._state(*key)
                state.last_cancel_reason = "busy_gate"
                snapshot = state.model_copy(deep=True)
            return Phase2AProactiveResponse(
                speak=False,
                reason="phase2a_busy_gate_blocked",
                session=self._session_payload(session),
                continuity=self._continuity_payload(snapshot),
            )

        config = self.config()
        episode_limit = int(config["limits"]["maximum_nudges_per_episode"])
        visit_limit = int(config["limits"]["maximum_nudges_per_visit"])
        with self._lock:
            state = self._state(*key)
            if not state.active or not state.episode_id:
                snapshot = state.model_copy(deep=True)
                return Phase2AProactiveResponse(
                    speak=False,
                    reason="no_active_sales_episode",
                    session=self._session_payload(session),
                    continuity=self._continuity_payload(snapshot),
                )
            if state.episode_nudge_count >= episode_limit:
                state.active = False
                state.last_planned_action = "episode_budget_exhausted"
                snapshot = state.model_copy(deep=True)
                return Phase2AProactiveResponse(
                    speak=False,
                    reason="episode_nudge_budget_exhausted",
                    session=self._session_payload(session),
                    continuity=self._continuity_payload(snapshot),
                )
            if state.total_nudge_count >= visit_limit:
                state.active = False
                state.last_planned_action = "visit_budget_exhausted"
                snapshot = state.model_copy(deep=True)
                return Phase2AProactiveResponse(
                    speak=False,
                    reason="visit_nudge_budget_exhausted",
                    session=self._session_payload(session),
                    continuity=self._continuity_payload(snapshot),
                )

            next_number = state.episode_nudge_count + 1
            state.episode_nudge_count = next_number
            state.total_nudge_count += 1

        if next_number == 1:
            text, expect, question_field, action = self._first_nudge(session, request.language)
            purpose: Literal["sales_phase2a_proactive_first", "sales_phase2a_proactive_second"] = _PHASE2A_FIRST
            delivery = Phase2AVoiceDelivery(
                style="curious_discovery" if expect else "confident_recommendation",
                pace="natural",
                energy="medium",
                question_tone="inviting" if expect else "none",
                pause_before_question=expect,
            )
        else:
            text, expect, question_field, action = self._second_nudge(session, request.language)
            purpose = _PHASE2A_SECOND
            delivery = Phase2AVoiceDelivery(
                style="curious_discovery" if expect else "confident_recommendation",
                pace="natural",
                energy="medium",
                question_tone="inviting" if expect else "none",
                pause_before_question=expect,
            )

        with self._lock:
            state = self._state(*key)
            state.last_planned_action = action
            if next_number >= episode_limit:
                state.active = True
            snapshot = state.model_copy(deep=True)

        reply = Phase2AProactiveReply(
            conversation_id=key[0],
            visit_id=key[1],
            language=request.language,
            purpose=purpose,
            text=text,
            fallback_text=text,
            expect_user_response=expect,
            question_field=question_field,
            delivery=delivery,
        )
        return Phase2AProactiveResponse(
            speak=True,
            reason=action,
            reply=reply,
            session=self._session_payload(sales_session_store.snapshot(*key) or session),
            continuity=self._continuity_payload(snapshot),
        )

    def status(self, conversation_id: str, visit_id: str) -> dict[str, Any]:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._state(*key).model_copy(deep=True)
        session = sales_session_store.snapshot(*key)
        return {
            "ok": True,
            "phase": "phase2a_sales_continuity",
            "identity": self.config()["identity"],
            "session": self._session_payload(session),
            "continuity": self._continuity_payload(state),
        }

    def self_test(self) -> dict[str, Any]:
        config = self.config()
        introductions = config["self_introduction"]
        forbidden_identity = config["forbidden_self_identity_terms"]
        forbidden_technology = [str(item).casefold() for item in config["forbidden_technology_terms"]]
        violations: list[str] = []
        for language in ("zh", "en"):
            text = str(introductions[language])
            lower = text.casefold()
            for term in forbidden_identity[language]:
                if str(term).casefold() in lower:
                    violations.append(f"{language}:identity:{term}")
            for term in forbidden_technology:
                if term in lower:
                    violations.append(f"{language}:technology:{term}")
        return {
            "ok": not violations,
            "phase": "phase2a_sales_continuity",
            "identity": config["identity"],
            "timing": config["timing"],
            "limits": config["limits"],
            "violations": violations,
        }

    def reset_for_test(self, conversation_id: str, visit_id: str) -> None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            self._states.pop(key, None)


sales_phase2a = SalesPhase2AService()
router = APIRouter(prefix="/api/sales/phase2a", tags=["sales-phase2a"])


@router.get("/self-introduction")
def phase2a_self_introduction(language: Language = Query(default="zh")) -> dict[str, Any]:
    try:
        return {
            "ok": True,
            "phase": "phase2a_sales_continuity",
            "identity": sales_phase2a.config()["identity"][language],
            "text": sales_phase2a.self_introduction(language),
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/output-result")
def phase2a_output_result(request: Phase2AOutputResultRequest) -> dict[str, Any]:
    try:
        state = sales_phase2a.record_output(request)
        return {
            "ok": True,
            "phase": "phase2a_sales_continuity",
            "continuity": sales_phase2a._continuity_payload(state),
        }
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/proactive", response_model=Phase2AProactiveResponse)
def phase2a_proactive(request: Phase2AProactiveRequest) -> Phase2AProactiveResponse:
    try:
        return sales_phase2a.plan_proactive(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status/{conversation_id}/{visit_id}")
def phase2a_status(conversation_id: str, visit_id: str) -> dict[str, Any]:
    return sales_phase2a.status(conversation_id, visit_id)


@router.get("/self-test")
def phase2a_self_test() -> dict[str, Any]:
    return sales_phase2a.self_test()
