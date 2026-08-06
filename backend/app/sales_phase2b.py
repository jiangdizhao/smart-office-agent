from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.sales_models import Language, SalesSessionPatch, SalesSessionState
from app.sales_policy import sales_runtime_policy
from app.sales_session_store import sales_session_store

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "sales_phase2b.json"

EngagementStage = Literal[
    "opening",
    "discovery",
    "context_understood",
    "recommendation",
    "experience",
    "value_confirmed",
    "conversion_ready",
    "booking_offered",
    "contact_offered",
    "interaction_active",
    "converted",
    "disengaged",
    "closing",
]
InteractionKind = Literal["meeting", "contact", "recording", "transcript", "results"]
InteractionResult = Literal["opened", "submitted", "failed", "cancelled", "closed"]


class Phase2BVoiceDelivery(BaseModel):
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
    ] = "warm_confident"
    pace: Literal["measured", "natural", "natural_brisk"] = "natural"
    energy: Literal["low", "medium", "medium_high"] = "medium"
    question_tone: Literal["none", "curious", "inviting"] = "none"
    emphasis_terms: list[str] = Field(default_factory=list, max_length=6)
    humour_delivery: Literal["none", "light_smile"] = "none"
    pause_before_question: bool = False


class Phase2BReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sales-phase2b-reply-v1"] = "sales-phase2b-reply-v1"
    conversation_id: str
    visit_id: str
    language: Language
    reply_mode: Literal["engagement", "proactive_sales", "conversion", "closing"]
    purpose: str = Field(..., min_length=1, max_length=160)
    text: str = Field(..., min_length=1, max_length=2_000)
    fallback_text: str = Field(..., min_length=1, max_length=2_000)
    expect_user_response: bool
    question_field: str | None = Field(default=None, max_length=120)
    humour_theme: str | None = Field(default=None, max_length=120)
    recommended_action: str | None = Field(default=None, max_length=120)
    delivery: Phase2BVoiceDelivery


class Phase2BObserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    language: Language = "zh"
    text: str = Field(..., min_length=1, max_length=12_000)
    recent_context: str = Field(default="", max_length=20_000)


class Phase2BOutputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    result: Literal["completed", "interrupted", "failed", "cancelled"]
    purpose: str = Field(default="", max_length=160)
    reply_mode: str = Field(default="", max_length=80)
    expect_user_response: bool = False
    question_field: str | None = Field(default=None, max_length=120)
    text: str = Field(default="", max_length=12_000)
    cancel_reason: str | None = Field(default=None, max_length=200)


class Phase2BInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    kind: InteractionKind
    result: InteractionResult
    verified: bool = False
    message: str = Field(default="", max_length=1_000)
    data: dict[str, Any] = Field(default_factory=dict)


class Phase2BProactiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    language: Language = "zh"
    user_speaking: bool = False
    agent_speaking: bool = False
    tool_active: bool = False
    interaction_input_active: bool = False


class Phase2BProactiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    phase: Literal["phase2b_engagement_conversion"] = "phase2b_engagement_conversion"
    speak: bool
    defer: bool = False
    retry_after_ms: int | None = Field(default=None, ge=250, le=60_000)
    reason: str
    reply: Phase2BReply | None = None
    engagement: dict[str, Any]
    session: dict[str, Any]


class _EngagementState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    visit_id: str
    stage: EngagementStage = "opening"
    episode_id: str | None = None
    episode_nudge_count: int = 0
    total_nudge_count: int = 0
    active: bool = False
    current_topic: str | None = None
    current_industry: str | None = None
    last_user_text: str | None = None
    last_output_purpose: str | None = None
    last_output_reply_mode: str | None = None
    last_output_result: str | None = None
    last_output_text_hash: str | None = None
    last_question_field: str | None = None
    last_plan_reason: str | None = None
    last_recommendation: str | None = None
    recommendation_count: int = 0
    interaction_kind: InteractionKind | None = None
    last_interaction_result: InteractionResult | None = None
    conversion_state: Literal[
        "none",
        "booking_pending",
        "contact_pending",
        "booking_completed",
        "contact_completed",
    ] = "none"
    busy_retry_count: int = 0
    updated_at_epoch: float = Field(default_factory=time.time)


class SalesPhase2BService:
    """Single Visit-level owner for engagement pacing and conversion progression.

    The service does not execute Office actions and does not replace the action safety
    gateway. It observes explicit visitor context, chooses a bounded next conversational
    step, and distinguishes panel opening from a verified booking/contact submission.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[tuple[str, str], _EngagementState] = {}
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
                    raise RuntimeError(f"Phase 2B configuration is invalid: {exc}") from exc
                if payload.get("schema_version") != "sales-phase2b-v1":
                    raise RuntimeError("Phase 2B configuration schema is unsupported.")
                self._config_cache = payload
            return dict(self._config_cache)

    def _state(self, conversation_id: str, visit_id: str) -> _EngagementState:
        key = self._key(conversation_id, visit_id)
        state = self._states.get(key)
        if state is None:
            state = _EngagementState(conversation_id=key[0], visit_id=key[1])
            self._states[key] = state
        return state

    @staticmethod
    def _hash(text: str) -> str | None:
        clean = " ".join(str(text or "").split())
        return hashlib.sha256(clean.encode("utf-8")).hexdigest() if clean else None

    @staticmethod
    def _session_payload(session: SalesSessionState | None) -> dict[str, Any]:
        return {} if session is None else session.model_dump(mode="json")

    def _payload(self, state: _EngagementState) -> dict[str, Any]:
        config = self.config()
        return {
            **state.model_dump(mode="json"),
            "timing": config["timing"],
            "limits": config["limits"],
        }

    def _industry_entry(self, industry_id: str | None) -> dict[str, Any]:
        entries = self.config()["industries"]
        selected = next(
            (item for item in entries if item.get("industry_id") == industry_id),
            None,
        )
        return selected or next(item for item in entries if item.get("industry_id") == "general")

    def _detect_industry(self, text: str) -> tuple[str | None, str | None]:
        clean = text.casefold()
        for entry in self.config()["industries"]:
            if entry.get("industry_id") == "general":
                continue
            for keyword in entry.get("keywords", []):
                if str(keyword).casefold() in clean:
                    return str(entry["industry_id"]), str(keyword)
        return None, None

    @staticmethod
    def _first_person_business_statement(text: str) -> bool:
        return bool(
            re.search(
                r"(?:我|我们|本公司|我们公司).{0,36}(?:在|从事|属于|做|负责|是一家|工作|项目|团队)|"
                r"\b(?:i|we|our company)\b.{0,90}\b(?:work|operate|manage|responsible|company|team|project)\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _extract_role(text: str) -> str | None:
        patterns = [
            r"(?:我是|我做|我担任|我负责|我的职位是)\s*([^，。！？,!?]{2,30})",
            r"\b(?:i am|i'm|my role is|i work as|i manage)\s+([^,.!?]{2,60})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return " ".join(match.group(1).strip().split())[:120]
        return None

    @staticmethod
    def _extract_pain(text: str) -> str | None:
        patterns = [
            r"(?:最麻烦的是|痛点是|问题是|难点是|最困扰的是|最浪费时间的是)\s*([^。！？!?]{3,180})",
            r"\b(?:the biggest problem is|our pain point is|the hardest part is|we lose time on)\s+([^.!?]{3,220})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return " ".join(match.group(1).strip().split())[:300]
        return None

    @staticmethod
    def _capabilities(text: str) -> list[str]:
        mapping = {
            "meeting_actions": ["会议", "纪要", "行动项", "meeting", "minutes", "action item"],
            "customer_follow_up": ["客户跟进", "销售跟进", "customer follow-up", "client follow-up"],
            "document_versioning": ["图纸", "版本", "文档", "drawing", "version", "document"],
            "email_assistance": ["邮件", "outlook", "email"],
            "presentation_control": ["ppt", "powerpoint", "演示文稿", "幻灯片"],
            "team_collaboration": ["teams", "协作", "跨地区", "handover", "collaboration"],
            "repetitive_administration": ["重复录入", "重复行政", "行政工作", "data entry", "repetitive administration"],
        }
        clean = text.casefold()
        return [
            capability
            for capability, keywords in mapping.items()
            if any(keyword.casefold() in clean for keyword in keywords)
        ]

    def observe_turn(self, request: Phase2BObserveRequest) -> dict[str, Any]:
        key = self._key(request.conversation_id, request.visit_id)
        text = " ".join(request.text.strip().split())
        session = sales_session_store.record_user_turn(
            *key,
            effective=True,
            language=request.language,
        )
        industry_id, industry_evidence = self._detect_industry(text)
        first_person = self._first_person_business_statement(text)
        role = self._extract_role(text) if first_person else None
        pain = self._extract_pain(text)
        capabilities = self._capabilities(text)
        explicit: dict[str, str] = {}
        if first_person and industry_id and industry_evidence:
            explicit["industry"] = industry_evidence
        if role:
            explicit["role"] = role
        if explicit or pain or capabilities:
            session = sales_session_store.apply_explicit_patch(
                *key,
                SalesSessionPatch(
                    explicit_facts=explicit,
                    pain_points=[pain] if pain else [],
                    interested_capabilities=capabilities,
                ),
            )

        with self._lock:
            state = self._state(*key)
            state.last_user_text = text[:500]
            if industry_id:
                state.current_industry = industry_id
                state.current_topic = industry_evidence
            elif capabilities:
                state.current_topic = capabilities[0]
            elif pain:
                state.current_topic = pain[:160]
            if state.stage in {"opening", "discovery"} and (
                state.current_industry or session.explicit_facts or session.pain_points or session.interested_capabilities
            ):
                state.stage = "context_understood"
            state.updated_at_epoch = time.time()
            snapshot = state.model_copy(deep=True)
            self._states[key] = state
        return {
            "ok": True,
            "phase": "phase2b_engagement_conversion",
            "observed": {
                "industry": industry_id,
                "first_person_business_statement": first_person,
                "role": role,
                "pain_point": pain,
                "interested_capabilities": capabilities,
            },
            "engagement": self._payload(snapshot),
            "session": self._session_payload(session),
        }

    @staticmethod
    def _terminal_output(request: Phase2BOutputRequest) -> bool:
        combined = f"{request.purpose} {request.reply_mode}".casefold()
        return request.reply_mode == "closing" or any(
            marker in combined
            for marker in ("visit_end", "farewell", "goodbye", "closing_output")
        )

    def record_output(self, request: Phase2BOutputRequest) -> dict[str, Any]:
        key = self._key(request.conversation_id, request.visit_id)
        session = sales_session_store.get_or_create(*key)
        with self._lock:
            state = self._state(*key)
            state.last_output_result = request.result
            state.last_output_purpose = request.purpose.strip() or "unknown_output"
            state.last_output_reply_mode = request.reply_mode.strip() or "general"
            state.last_output_text_hash = self._hash(request.text)
            state.last_question_field = request.question_field
            state.updated_at_epoch = time.time()

            if request.result != "completed":
                state.active = False
                self._states[key] = state
                return {"ok": True, "engagement": self._payload(state), "session": self._session_payload(session)}

            if self._terminal_output(request) or session.disengaged or session.stage == "close":
                state.stage = "closing"
                state.active = False
                self._states[key] = state
                return {"ok": True, "engagement": self._payload(state), "session": self._session_payload(session)}

            if request.question_field == "booking":
                state.stage = "booking_offered"
                state.conversion_state = "booking_pending"
                state.active = False
            elif request.question_field == "contact":
                state.stage = "contact_offered"
                state.conversion_state = "contact_pending"
                state.active = False
            elif request.purpose == "sales_phase2b_retreat":
                state.stage = "disengaged"
                state.active = False
            elif request.purpose == "sales_phase2b_proactive_first":
                state.active = True
            elif request.purpose == "sales_phase2b_proactive_second":
                state.active = False
            else:
                state.episode_id = uuid4().hex
                state.episode_nudge_count = 0
                state.active = True
                if state.stage == "opening":
                    state.stage = "discovery"
            self._states[key] = state
            snapshot = state.model_copy(deep=True)
        return {"ok": True, "engagement": self._payload(snapshot), "session": self._session_payload(session)}

    def record_interaction(self, request: Phase2BInteractionRequest) -> dict[str, Any]:
        key = self._key(request.conversation_id, request.visit_id)
        session = sales_session_store.get_or_create(*key)
        with self._lock:
            state = self._state(*key)
            state.interaction_kind = request.kind
            state.last_interaction_result = request.result
            state.updated_at_epoch = time.time()
            if request.result == "opened":
                state.stage = "interaction_active"
                state.active = False
            elif request.result == "submitted" and request.verified:
                if request.kind == "meeting":
                    session = sales_session_store.mark_booking_opened(*key)
                    state.conversion_state = "booking_completed"
                elif request.kind == "contact":
                    session = sales_session_store.mark_contact_opened(*key)
                    session = sales_session_store.mark_profile_persisted(*key)
                    state.conversion_state = "contact_completed"
                state.stage = "converted"
                state.active = False
            elif request.result in {"failed", "cancelled", "closed"}:
                state.interaction_kind = None
                if state.conversion_state not in {"booking_completed", "contact_completed"}:
                    state.stage = "value_confirmed" if session.value_delivered else "discovery"
                    state.active = True
                    state.episode_id = uuid4().hex
                    state.episode_nudge_count = 0
            self._states[key] = state
            snapshot = state.model_copy(deep=True)
        return {"ok": True, "engagement": self._payload(snapshot), "session": self._session_payload(session)}

    @staticmethod
    def _context_available(state: _EngagementState, session: SalesSessionState) -> bool:
        return bool(
            state.current_industry
            or state.current_topic
            or session.explicit_facts
            or session.pain_points
            or session.interested_capabilities
        )

    @staticmethod
    def _conversion_ready(state: _EngagementState, session: SalesSessionState) -> bool:
        return bool(
            session.value_delivered
            and SalesPhase2BService._context_available(state, session)
            and (
                session.effective_user_turn_count >= 2
                or session.pain_points
                or session.interested_capabilities
            )
        )

    def _humour(
        self,
        state: _EngagementState,
        session: SalesSessionState,
        language: Language,
        theme: str,
    ) -> tuple[str | None, str | None]:
        flags = sales_runtime_policy.feature_flags()
        if not flags.humour_enabled or session.effective_user_turn_count < 2:
            return None, None
        entry = next(
            (item for item in self.config()["humour"] if item.get("theme_id") == theme),
            None,
        )
        if not entry:
            return None, None
        text = str(entry[language]).strip()
        allowed, _ = sales_session_store.record_humour(
            state.conversation_id,
            state.visit_id,
            theme=theme,
            text=text,
            minimum_turn_gap=3,
        )
        return (text, theme) if allowed else (None, None)

    def _recommendation(
        self,
        state: _EngagementState,
        session: SalesSessionState,
        language: Language,
    ) -> tuple[str, str, str | None, str | None]:
        entry = self._industry_entry(state.current_industry)
        recommendation = str(entry[f"recommendation_{language}"]).strip()
        question = str(entry[f"question_{language}"]).strip()
        pain = session.pain_points[0] if session.pain_points else ""
        if language == "zh":
            lead = f"结合您刚才提到的“{pain}”，" if pain else "结合当前这个场景，"
            text = f"{lead}{recommendation}"
        else:
            lead = f"Based on the issue you mentioned — {pain} — " if pain else "For this situation, "
            text = f"{lead}{recommendation}"
        humour, theme = self._humour(
            state,
            session,
            language,
            str(entry.get("humour_theme") or "meeting_memory"),
        )
        parts = [text]
        if humour:
            parts.append(humour)
        parts.append(question)
        return " ".join(parts), question, humour, theme

    def _discovery(self, language: Language) -> tuple[str, str]:
        if language == "en":
            question = "Which part takes the most time today: meetings, customer follow-up or repetitive administration?"
            return "I can make this more relevant without turning it into a questionnaire. " + question, question
        question = "目前最占时间的是会议、客户跟进，还是重复行政工作？"
        return "我可以把交流继续收窄到您的真实场景，而且不会把它变成一份问卷。" + question, question

    def _booking_offer(
        self,
        state: _EngagementState,
        session: SalesSessionState,
        language: Language,
    ) -> str | None:
        if not sales_runtime_policy.can_offer_booking(session):
            return None
        offered, updated = sales_session_store.offer_booking(state.conversation_id, state.visit_id)
        if not offered:
            return None
        context = updated.pain_points[0] if updated.pain_points else state.current_topic or "the workflow discussed"
        if language == "zh":
            return f"刚才已经把“{context}”收窄成一个可验证的切入点。需要我现在打开预约日历，安排一次使用真实流程的完整体验吗？"
        return f"We have narrowed {context} to a verifiable starting point. Shall I open the booking calendar for a complete session using a real workflow?"

    def _contact_offer(
        self,
        state: _EngagementState,
        session: SalesSessionState,
        language: Language,
    ) -> str | None:
        if not sales_runtime_policy.can_offer_contact(session):
            return None
        offered, _ = sales_session_store.offer_contact(state.conversation_id, state.visit_id)
        if not offered:
            return None
        if language == "zh":
            return "现在不预约也没关系。需要我打开登记信息表，让顾问根据我们刚才讨论的场景后续联系吗？"
        return "There is no need to book now. Shall I open the contact form so a consultant can follow up on the scenario we discussed?"

    def plan_proactive(self, request: Phase2BProactiveRequest) -> Phase2BProactiveResponse:
        key = self._key(request.conversation_id, request.visit_id)
        session = sales_session_store.get_or_create(*key, language=request.language)
        with self._lock:
            state = self._state(*key)
            snapshot = state.model_copy(deep=True)

        flags = sales_runtime_policy.feature_flags()
        if not flags.agent_enabled or not flags.proactive_enabled:
            return Phase2BProactiveResponse(
                speak=False,
                reason="phase2b_proactive_disabled",
                engagement=self._payload(snapshot),
                session=self._session_payload(session),
            )
        if request.user_speaking or request.agent_speaking or request.tool_active or request.interaction_input_active:
            retry = int(float(self.config()["timing"]["busy_retry_seconds"]) * 1000)
            with self._lock:
                state = self._state(*key)
                state.busy_retry_count += 1
                state.last_plan_reason = "busy_deferred"
                self._states[key] = state
                snapshot = state.model_copy(deep=True)
            return Phase2BProactiveResponse(
                speak=False,
                defer=True,
                retry_after_ms=retry,
                reason="phase2b_busy_deferred",
                engagement=self._payload(snapshot),
                session=self._session_payload(session),
            )
        if not snapshot.active or snapshot.stage in {"converted", "disengaged", "closing", "interaction_active"}:
            return Phase2BProactiveResponse(
                speak=False,
                reason=f"phase2b_not_eligible:{snapshot.stage}",
                engagement=self._payload(snapshot),
                session=self._session_payload(session),
            )

        limits = self.config()["limits"]
        if snapshot.episode_nudge_count >= int(limits["maximum_nudges_per_episode"]):
            with self._lock:
                state = self._state(*key)
                state.active = False
                state.last_plan_reason = "episode_budget_exhausted"
                self._states[key] = state
                snapshot = state.model_copy(deep=True)
            return Phase2BProactiveResponse(
                speak=False,
                reason="phase2b_episode_budget_exhausted",
                engagement=self._payload(snapshot),
                session=self._session_payload(session),
            )
        if snapshot.total_nudge_count >= int(limits["maximum_nudges_per_visit"]):
            with self._lock:
                state = self._state(*key)
                state.active = False
                state.stage = "disengaged"
                state.last_plan_reason = "visit_budget_exhausted"
                self._states[key] = state
                snapshot = state.model_copy(deep=True)
            return Phase2BProactiveResponse(
                speak=False,
                reason="phase2b_visit_budget_exhausted",
                engagement=self._payload(snapshot),
                session=self._session_payload(session),
            )

        purpose = "sales_phase2b_proactive_first" if snapshot.episode_nudge_count == 0 else "sales_phase2b_proactive_second"
        question_field: str | None = None
        recommended_action: str | None = None
        humour_theme: str | None = None
        reply_mode: Literal["engagement", "proactive_sales", "conversion", "closing"] = "proactive_sales"

        if not self._context_available(snapshot, session):
            text, _ = self._discovery(request.language)
            question_field = "office_pain_points"
            reason = "contextual_discovery"
            delivery = Phase2BVoiceDelivery(
                style="curious_discovery",
                question_tone="curious",
                pause_before_question=True,
            )
            next_stage: EngagementStage = "discovery"
        elif not session.value_delivered or snapshot.recommendation_count == 0:
            text, _, humour, humour_theme = self._recommendation(snapshot, session, request.language)
            question_field = "office_pain_points"
            reason = "scenario_recommendation"
            session = sales_session_store.mark_value_delivered(*key, action="phase2b_scenario_recommendation")
            try:
                session = sales_session_store.transition(*key, "recommend", action="phase2b_scenario_recommendation")
            except ValueError:
                pass
            delivery = Phase2BVoiceDelivery(
                style="light_playful" if humour else "confident_recommendation",
                question_tone="inviting",
                humour_delivery="light_smile" if humour else "none",
                pause_before_question=True,
            )
            next_stage = "recommendation"
        elif self._conversion_ready(snapshot, session):
            if session.booking_rejected:
                text = self._contact_offer(snapshot, session, request.language) or ""
                if text:
                    question_field = "contact"
                    recommended_action = "offer_contact"
                    reason = "contextual_contact_offer_after_booking_rejection"
                    reply_mode = "conversion"
                    delivery = Phase2BVoiceDelivery(
                        style="calm_reassuring",
                        question_tone="inviting",
                        pause_before_question=True,
                    )
                    next_stage = "contact_offered"
                else:
                    text = "您可以先自由参观，需要时直接告诉我就可以。" if request.language == "zh" else "Please feel free to look around and speak to me whenever you need anything."
                    reason = "low_pressure_retreat"
                    purpose = "sales_phase2b_retreat"
                    reply_mode = "closing"
                    delivery = Phase2BVoiceDelivery(style="calm_reassuring", energy="low")
                    next_stage = "disengaged"
            else:
                text = self._booking_offer(snapshot, session, request.language) or ""
                if text:
                    question_field = "booking"
                    recommended_action = "offer_booking"
                    reason = "contextual_booking_offer"
                    reply_mode = "conversion"
                    delivery = Phase2BVoiceDelivery(
                        style="confident_recommendation",
                        question_tone="inviting",
                        pause_before_question=True,
                    )
                    next_stage = "booking_offered"
                else:
                    text = "我可以继续把这个场景拆成一个具体流程，也可以在您准备好时安排完整体验。" if request.language == "zh" else "I can break this scenario into a concrete workflow, or arrange a complete session when you are ready."
                    reason = "experience_choice"
                    delivery = Phase2BVoiceDelivery(style="warm_confident")
                    next_stage = "experience"
        else:
            text = "我可以继续用一个具体例子说明，也可以先让您自由参观。" if request.language == "zh" else "I can continue with a concrete example, or let you explore freely for now."
            reason = "low_pressure_value_follow_up"
            delivery = Phase2BVoiceDelivery(style="warm_confident", energy="low")
            next_stage = "value_confirmed"

        with self._lock:
            state = self._state(*key)
            state.episode_nudge_count += 1
            state.total_nudge_count += 1
            state.last_plan_reason = reason
            state.stage = next_stage
            state.busy_retry_count = 0
            if reason == "scenario_recommendation":
                state.recommendation_count += 1
                state.last_recommendation = text[:500]
            if question_field in {"booking", "contact"} or next_stage in {"disengaged", "converted"}:
                state.active = False
            self._states[key] = state
            snapshot = state.model_copy(deep=True)

        reply = Phase2BReply(
            conversation_id=key[0],
            visit_id=key[1],
            language=request.language,
            reply_mode=reply_mode,
            purpose=purpose,
            text=text,
            fallback_text=text,
            expect_user_response=question_field is not None,
            question_field=question_field,
            humour_theme=humour_theme,
            recommended_action=recommended_action,
            delivery=delivery,
        )
        return Phase2BProactiveResponse(
            speak=True,
            reason=reason,
            reply=reply,
            engagement=self._payload(snapshot),
            session=self._session_payload(sales_session_store.snapshot(*key) or session),
        )

    def status(self, conversation_id: str, visit_id: str) -> dict[str, Any]:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._state(*key).model_copy(deep=True)
        return {
            "ok": True,
            "phase": "phase2b_engagement_conversion",
            "engagement": self._payload(state),
            "session": self._session_payload(sales_session_store.snapshot(*key)),
        }

    def end_visit(self, conversation_id: str, visit_id: str) -> None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            self._states.pop(key, None)

    def self_test(self) -> dict[str, Any]:
        config = self.config()
        violations: list[str] = []
        if int(config["limits"]["maximum_nudges_per_episode"]) != 2:
            violations.append("episode_budget_must_be_two")
        if int(config["limits"]["maximum_nudges_per_visit"]) < 2:
            violations.append("visit_budget_too_small")
        for entry in config["humour"]:
            if not str(entry.get("zh") or "").strip() or not str(entry.get("en") or "").strip():
                violations.append(f"empty_humour:{entry.get('theme_id')}")
        return {
            "ok": not violations,
            "phase": "phase2b_engagement_conversion",
            "single_visit_owner": True,
            "all_customer_outputs_can_rearm": True,
            "busy_policy": "defer_not_cancel",
            "conversion_verification": "submitted_and_verified",
            "timing": config["timing"],
            "limits": config["limits"],
            "violations": violations,
        }


sales_phase2b = SalesPhase2BService()
router = APIRouter(prefix="/api/sales/phase2b", tags=["sales-phase2b"])


@router.post("/observe-turn")
def phase2b_observe_turn(request: Phase2BObserveRequest) -> dict[str, Any]:
    try:
        return sales_phase2b.observe_turn(request)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/output-result")
def phase2b_output_result(request: Phase2BOutputRequest) -> dict[str, Any]:
    try:
        return sales_phase2b.record_output(request)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/interaction-result")
def phase2b_interaction_result(request: Phase2BInteractionRequest) -> dict[str, Any]:
    try:
        return sales_phase2b.record_interaction(request)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/proactive", response_model=Phase2BProactiveResponse)
def phase2b_proactive(request: Phase2BProactiveRequest) -> Phase2BProactiveResponse:
    try:
        return sales_phase2b.plan_proactive(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status/{conversation_id}/{visit_id}")
def phase2b_status(conversation_id: str, visit_id: str) -> dict[str, Any]:
    return sales_phase2b.status(conversation_id, visit_id)


@router.delete("/visit/{conversation_id}/{visit_id}")
def phase2b_end_visit(conversation_id: str, visit_id: str) -> dict[str, Any]:
    sales_phase2b.end_visit(conversation_id, visit_id)
    return {"ok": True, "phase": "phase2b_engagement_conversion"}


@router.get("/self-test")
def phase2b_self_test() -> dict[str, Any]:
    return sales_phase2b.self_test()
