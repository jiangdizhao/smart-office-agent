from __future__ import annotations

import hashlib
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.sales_config import sales_config
from app.sales_models import Language, SalesSessionState
from app.sales_policy import sales_runtime_policy
from app.sales_session_store import sales_session_store

GreetingKind = Literal[
    "new_anonymous",
    "returning_anonymous",
    "registered_identity",
]
VoiceStyle = Literal[
    "warm_confident",
    "curious_discovery",
    "confident_recommendation",
    "light_playful",
    "calm_reassuring",
    "verified_success",
    "neutral_exact",
]
VoicePace = Literal["measured", "natural", "natural_brisk"]


class VoiceDeliveryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["voice-delivery-v1"] = "voice-delivery-v1"
    style: VoiceStyle = "warm_confident"
    pace: VoicePace = "natural"
    energy: Literal["low", "medium", "medium_high"] = "medium"
    question_tone: Literal["none", "curious", "inviting"] = "none"
    emphasis_terms: list[str] = Field(default_factory=list, max_length=6)
    humour_delivery: Literal["none", "light_smile"] = "none"
    pause_before_question: bool = False


class SalesExperienceReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sales-experience-reply-v1"] = "sales-experience-reply-v1"
    conversation_id: str
    visit_id: str
    language: Language
    reply_mode: Literal["opening", "proactive_sales"]
    purpose: Literal["sales_opening", "sales_proactive_first", "sales_proactive_second"]
    text: str
    fallback_text: str
    expect_user_response: bool
    question_field: str | None = None
    humour_theme: str | None = None
    delivery: VoiceDeliveryPlan


class SalesExperienceProactiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    language: Language = "zh"
    user_speaking: bool = False
    agent_speaking: bool = False
    tool_active: bool = False
    interaction_input_active: bool = False


class SalesExperienceProactiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    phase: Literal["phase1_sales_experience"] = "phase1_sales_experience"
    speak: bool
    reason: str
    reply: SalesExperienceReply | None = None
    session: SalesSessionState


class _ExperienceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    visit_id: str
    language: Language = "zh"
    greeting_kind: GreetingKind = "new_anonymous"
    opening_completed: bool = False
    opening_humour_used: bool = False
    pending_question_field: str | None = None
    pending_question_text: str | None = None
    last_reply_mode: str | None = None
    last_delivery_style: str | None = None
    last_output_result: str | None = None
    last_cancel_reason: str | None = None


class SalesExperienceService:
    """Customer-facing Visit experience for opening and silence follow-up.

    This service intentionally owns only customer experience state. Business facts,
    invitation budgets and humour history remain authoritative in SalesSessionStore.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[tuple[str, str], _ExperienceState] = {}

    @staticmethod
    def _key(conversation_id: str, visit_id: str) -> tuple[str, str]:
        conversation = str(conversation_id or "").strip()
        visit = str(visit_id or "").strip()
        if not conversation or not visit:
            raise ValueError("conversation_id and visit_id are required")
        return conversation, visit

    @staticmethod
    def _stable_index(seed: str, size: int) -> int:
        if size <= 1:
            return 0
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % size

    @staticmethod
    def _identity(language: Language) -> str:
        bundle = sales_config.require_valid()
        return bundle.persona.identity.model_dump()[language]

    @staticmethod
    def _question(language: Language, field: str) -> str:
        bundle = sales_config.require_valid()
        for item in bundle.playbooks.discovery_fields:
            if str(item.get("field") or "") != field:
                continue
            questions = item.get("questions", {}).get(language, [])
            clean = [" ".join(str(value).strip().split()) for value in questions if str(value).strip()]
            if clean:
                return clean[0]
        if field == "role":
            return (
                "What kind of work are you mainly responsible for?"
                if language == "en"
                else "您主要从事什么工作？"
            )
        if language == "en":
            return "Which office workflow takes the most time for your team today?"
        return "目前哪一种办公流程最浪费您团队的时间？"

    def _opening_humour(
        self,
        *,
        conversation_id: str,
        visit_id: str,
        language: Language,
        greeting_kind: GreetingKind,
    ) -> tuple[str | None, str | None, SalesSessionState]:
        state = sales_session_store.get_or_create(conversation_id, visit_id, language=language)
        flags = sales_runtime_policy.feature_flags()
        if not flags.humour_enabled or greeting_kind == "registered_identity":
            return None, None, state
        themes = (
            ["returning_visitor"]
            if greeting_kind == "returning_anonymous"
            else ["digital_employee_intro", "office_resources", "multilingual"]
        )
        theme = themes[self._stable_index(f"{conversation_id}|{visit_id}|opening", len(themes))]
        text = sales_runtime_policy.approved_humour_text(
            theme=theme,
            language=language,
            seed=f"{conversation_id}|{visit_id}|opening",
        )
        if not text:
            return None, None, state
        recorded, state = sales_session_store.record_humour(
            conversation_id,
            visit_id,
            theme=theme,
            text=text,
            minimum_turn_gap=1,
        )
        return (text, theme, state) if recorded else (None, None, state)

    def plan_opening(
        self,
        *,
        conversation_id: str,
        visit_id: str,
        language: Language,
        greeting_kind: GreetingKind,
        display_name: str | None = None,
    ) -> SalesExperienceReply:
        key = self._key(conversation_id, visit_id)
        identity = self._identity(language)
        name = " ".join(str(display_name or "").strip().split())[:80]

        # Exhibition visitors have very little time. Ask for their role immediately;
        # industry, pain points and feature interests remain optional facts that can
        # be captured naturally rather than becoming a long questionnaire.
        question_field = "role"
        question = self._question(language, question_field)
        sales_session_store.mark_field_asked(conversation_id, visit_id, question_field)

        if language == "en":
            if greeting_kind == "registered_identity" and name:
                lead = f"Welcome back, {name}. I am Sara, your {identity}."
            elif greeting_kind == "returning_anonymous":
                lead = f"Welcome back. I am Sara, your {identity}."
            else:
                lead = f"Welcome to our office. I am Sara, your {identity}."
            text = " ".join((lead, question))
        else:
            if greeting_kind == "registered_identity" and name:
                lead = f"欢迎回来，{name}。我是 Sara，公司的{identity}。"
            elif greeting_kind == "returning_anonymous":
                lead = f"欢迎回来，我是 Sara，公司的{identity}。"
            else:
                lead = f"您好，欢迎来到我们的办公室，我是 Sara，公司的{identity}。"
            text = f"{lead}{question}"

        delivery = VoiceDeliveryPlan(
            style="light_playful",
            pace="natural_brisk",
            energy="medium_high",
            question_tone="curious",
            emphasis_terms=(
                ["Digital Manager", "Enterprise Solution Consultant"]
                if language == "en"
                else ["数字管理员", "企业解决方案顾问"]
            ),
            humour_delivery="light_smile",
            pause_before_question=True,
        )
        with self._lock:
            self._states[key] = _ExperienceState(
                conversation_id=conversation_id,
                visit_id=visit_id,
                language=language,
                greeting_kind=greeting_kind,
                opening_completed=True,
                opening_humour_used=False,
                pending_question_field=question_field,
                pending_question_text=question,
                last_reply_mode="opening",
                last_delivery_style=delivery.style,
                last_output_result="planned",
            )
        return SalesExperienceReply(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language=language,
            reply_mode="opening",
            purpose="sales_opening",
            text=text,
            fallback_text=text,
            expect_user_response=True,
            question_field=question_field,
            humour_theme=None,
            delivery=delivery,
        )

    def plan_proactive(
        self,
        request: SalesExperienceProactiveRequest,
    ) -> SalesExperienceProactiveResponse:
        key = self._key(request.conversation_id, request.visit_id)
        session = sales_session_store.get_or_create(
            request.conversation_id,
            request.visit_id,
            language=request.language,
        )
        flags = sales_runtime_policy.feature_flags()
        if not flags.agent_enabled or not flags.proactive_enabled:
            return SalesExperienceProactiveResponse(
                speak=False,
                reason="proactive_sales_disabled",
                session=session,
            )
        if not sales_runtime_policy.can_proactively_nudge(
            session,
            user_speaking=request.user_speaking,
            agent_speaking=request.agent_speaking,
            tool_active=request.tool_active,
            interaction_input_active=request.interaction_input_active,
        ):
            return SalesExperienceProactiveResponse(
                speak=False,
                reason="proactive_policy_gate_blocked",
                session=session,
            )
        with self._lock:
            experience = self._states.get(key)
        if experience is None or not experience.opening_completed:
            return SalesExperienceProactiveResponse(
                speak=False,
                reason="sales_opening_not_completed",
                session=session,
            )

        allowed, session = sales_session_store.record_proactive_nudge(
            request.conversation_id,
            request.visit_id,
        )
        if not allowed:
            return SalesExperienceProactiveResponse(
                speak=False,
                reason="proactive_nudge_budget_exhausted",
                session=session,
            )

        role = str(session.explicit_facts.get("role") or "").strip()
        contact_can_be_offered = bool(role) and sales_runtime_policy.can_offer_contact(session)
        offered = False
        if contact_can_be_offered:
            offered, session = sales_session_store.offer_contact(
                request.conversation_id,
                request.visit_id,
            )

        if not role and session.proactive_nudge_count == 1:
            text = (
                "What kind of work are you mainly responsible for? I can tailor the demonstration to your role."
                if request.language == "en"
                else "您主要负责哪类工作？我可以直接按您的岗位来演示。"
            )
            purpose: Literal["sales_proactive_first", "sales_proactive_second"] = "sales_proactive_first"
            expect_user_response = True
            question_field = "role"
            delivery = VoiceDeliveryPlan(
                style="curious_discovery",
                pace="natural_brisk",
                energy="medium",
                question_tone="inviting",
                pause_before_question=True,
            )
        elif offered:
            text = (
                "Shall I open visitor registration so a consultant can follow up on your work scenario?"
                if request.language == "en"
                else "需要我打开登记信息表，让顾问根据您的工作场景继续联系吗？"
            )
            purpose = "sales_proactive_second"
            expect_user_response = True
            question_field = "contact_consent"
            delivery = VoiceDeliveryPlan(
                style="warm_confident",
                pace="natural_brisk",
                energy="medium",
                question_tone="inviting",
                pause_before_question=True,
            )
        else:
            text = (
                "You can say ‘open PowerPoint’ or ‘start the presentation’ and I will demonstrate it directly."
                if request.language == "en"
                else "您可以直接说“打开 PPT”或“播放演示”，我会马上操作。"
            )
            purpose = "sales_proactive_second"
            expect_user_response = False
            question_field = None
            delivery = VoiceDeliveryPlan(
                style="calm_reassuring",
                pace="natural_brisk",
                energy="low",
                question_tone="none",
                pause_before_question=False,
            )

        with self._lock:
            experience.pending_question_field = question_field
            experience.pending_question_text = text if expect_user_response else None
            experience.last_reply_mode = "proactive_sales"
            experience.last_delivery_style = delivery.style
            experience.last_output_result = "planned"
            self._states[key] = experience
        reply = SalesExperienceReply(
            conversation_id=request.conversation_id,
            visit_id=request.visit_id,
            language=request.language,
            reply_mode="proactive_sales",
            purpose=purpose,
            text=text,
            fallback_text=text,
            expect_user_response=expect_user_response,
            question_field=question_field,
            delivery=delivery,
        )
        return SalesExperienceProactiveResponse(
            speak=True,
            reason=purpose,
            reply=reply,
            session=session,
        )

    def mark_output_result(
        self,
        conversation_id: str,
        visit_id: str,
        result: str,
    ) -> None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._states.get(key)
            if state is not None:
                state.last_output_result = " ".join(str(result).split())[:120]
                self._states[key] = state

    def mark_cancel_reason(
        self,
        conversation_id: str,
        visit_id: str,
        reason: str,
    ) -> None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._states.get(key)
            if state is not None:
                state.last_cancel_reason = " ".join(str(reason).split())[:160]
                self._states[key] = state

    def status(self, conversation_id: str, visit_id: str) -> dict:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            experience = self._states.get(key)
        session = sales_session_store.snapshot(conversation_id, visit_id)
        return {
            "ok": experience is not None,
            "phase": "phase1_sales_experience",
            "persona": self._identity("zh"),
            "timing": {"first_nudge_seconds": 7, "second_delay_seconds": 8},
            "experience": None if experience is None else experience.model_dump(mode="json"),
            "sales_session": None if session is None else session.model_dump(mode="json"),
        }

    def self_test(self) -> dict:
        bundle = sales_config.require_valid()
        themes = {
            str(item.get("theme_id") or "")
            for item in bundle.claims.humour_themes
            if isinstance(item, dict)
        }
        required = {"digital_employee_intro", "office_resources", "multilingual"}
        return {
            "ok": required.issubset(themes),
            "phase": "phase1_sales_experience",
            "persona": bundle.persona.identity.model_dump(mode="json"),
            "opening_humour_themes_present": sorted(required & themes),
            "first_nudge_seconds": 7,
            "second_delay_seconds": 8,
            "maximum_nudges_per_visit": 2,
            "voice_delivery_schema": VoiceDeliveryPlan.model_json_schema(),
        }

    def end_visit(self, conversation_id: str, visit_id: str) -> None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            self._states.pop(key, None)


sales_experience = SalesExperienceService()
