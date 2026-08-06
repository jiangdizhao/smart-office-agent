from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.sales_config import SalesConfigBundle, sales_config
from app.sales_models import (
    HumourDirective,
    Language,
    SalesProfileExtraction,
    SalesProactiveRequest,
    SalesProactiveResponse,
    SalesReplyPlan,
    SalesSessionPatch,
    SalesSessionState,
    SalesStage,
    SalesTurnRequest,
    SalesTurnResponse,
)
from app.sales_policy import sales_runtime_policy
from app.sales_profile_extractor import sales_profile_extractor
from app.sales_profile_persistence import sales_profile_persistence
from app.sales_session_store import sales_session_store
from app.sales_telemetry import sales_telemetry


@dataclass(frozen=True)
class _ReplyMaterial:
    capability_ids: list[str]
    approved_claims: list[str]
    prohibited_claims: list[str]
    pitch: str


def _clean(value: Any, maximum: int = 500) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


def _unique(values: list[str], maximum: int = 20) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = _clean(value, 300)
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= maximum:
            break
    return result


def _sentence_join(parts: list[str], language: Language, maximum_sentences: int = 4) -> str:
    cleaned = [_clean(item, 800).rstrip("。.!！?") for item in parts if _clean(item, 800)]
    punctuation = "." if language == "en" else "。"
    return punctuation.join(cleaned[:maximum_sentences]) + (punctuation if cleaned else "")


class SalesReplyPlanner:
    def _bundle(self) -> SalesConfigBundle:
        return sales_config.require_valid()

    @staticmethod
    def _safe_transition(
        state: SalesSessionState,
        stage: SalesStage,
        action: str,
    ) -> SalesSessionState:
        if state.stage == "close":
            return state
        try:
            return sales_session_store.transition(
                state.conversation_id,
                state.visit_id,
                stage,
                action=action,
            )
        except ValueError:
            return state

    @staticmethod
    def _profile_context(state: SalesSessionState) -> list[str]:
        result = [f"{key}: {value}" for key, value in sorted(state.explicit_facts.items())]
        result.extend(f"pain_point: {item}" for item in state.pain_points)
        result.extend(f"interest: {item}" for item in state.interested_capabilities)
        result.extend(f"objection: {item}" for item in state.objections)
        return result

    @staticmethod
    def _playbook(bundle: SalesConfigBundle, playbook_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in bundle.playbooks.playbooks
                if str(item.get("playbook_id") or "") == playbook_id
            ),
            None,
        )

    def _capability_ids(
        self,
        extraction: SalesProfileExtraction,
        state: SalesSessionState,
        bundle: SalesConfigBundle,
    ) -> list[str]:
        values = list(extraction.interested_capabilities)
        for playbook_id in extraction.matched_playbooks:
            playbook = self._playbook(bundle, playbook_id)
            if isinstance(playbook, dict):
                values.extend(str(item) for item in playbook.get("capabilities", []))
        values.extend(state.interested_capabilities)
        return _unique(values, maximum=3)

    def _material(
        self,
        capability_ids: list[str],
        language: Language,
    ) -> _ReplyMaterial:
        approved: list[str] = []
        prohibited = [
            "never_claim_unverified_execution",
            "never_invent_customer_facts",
            "maximum_one_question",
        ]
        pitches: list[str] = []
        valid_ids: list[str] = []
        for capability_id in capability_ids[:3]:
            capability = sales_config.capability(capability_id)
            if capability is None:
                prohibited.append(f"do_not_invent:{capability_id}")
                continue
            valid_ids.append(capability.capability_id)
            approved.extend(capability.customer_value.get(language, []))
            prohibited.extend(capability.prohibited_claims)
            pitch = capability.approved_pitch.model_dump().get(language)
            if pitch:
                pitches.append(str(pitch))
        return _ReplyMaterial(
            capability_ids=valid_ids,
            approved_claims=_unique(approved, maximum=8),
            prohibited_claims=sorted(set(prohibited)),
            pitch=_clean(pitches[0], 800) if pitches else "",
        )

    @staticmethod
    def _field_has_value(state: SalesSessionState, field: str) -> bool:
        if field == "office_pain_points":
            return bool(state.pain_points)
        if field == "interested_capabilities":
            return bool(state.interested_capabilities)
        return bool(state.explicit_facts.get(field))

    def _next_discovery_question(
        self,
        state: SalesSessionState,
        language: Language,
        bundle: SalesConfigBundle,
    ) -> tuple[str | None, SalesSessionState]:
        order = ["industry", "role", "office_pain_points", "interested_capabilities"]
        by_field = {
            str(item.get("field") or ""): item
            for item in bundle.playbooks.discovery_fields
            if isinstance(item, dict)
        }
        for field in order:
            if self._field_has_value(state, field):
                continue
            if field in state.asked_fields or field in state.declined_fields:
                continue
            allowed, updated = sales_session_store.mark_field_asked(
                state.conversation_id,
                state.visit_id,
                field,
            )
            if not allowed:
                state = updated
                continue
            questions = by_field.get(field, {}).get("questions", {}).get(language, [])
            questions = [_clean(item, 500) for item in questions if _clean(item, 500)]
            if not questions:
                return None, updated
            return questions[(updated.turn_count - 1) % len(questions)], updated
        return None, state

    @staticmethod
    def _humour_choice(capability_ids: list[str], extraction: SalesProfileExtraction) -> tuple[str, str] | None:
        if extraction.ordinary_chatbot_objection:
            return "low_risk_explanation", "not_ordinary_chatbot"
        if any(item in capability_ids for item in ("meeting_summary", "teams_collaboration")):
            return "meeting_management", "meeting_management"
        if "presentation_automation" in capability_ids:
            return "low_risk_explanation", "powerpoint"
        if "product_planning" in capability_ids:
            return "low_risk_explanation", "product_planning"
        if "enterprise_custom_integration" in capability_ids:
            return "low_risk_explanation", "enterprise_system_personality"
        if any(item in capability_ids for item in ("office_automation", "activity_management")):
            return "low_risk_explanation", "repetitive_office_work"
        return None

    def _select_humour(
        self,
        state: SalesSessionState,
        extraction: SalesProfileExtraction,
        capability_ids: list[str],
        language: Language,
    ) -> tuple[HumourDirective, SalesSessionState]:
        if extraction.cost_question or extraction.privacy_question or extraction.disengaged:
            return HumourDirective(reason="sensitive_or_cost_specific_context"), state
        choice = self._humour_choice(capability_ids, extraction)
        if choice is None:
            return HumourDirective(reason="no_relevant_approved_theme"), state
        context, theme = choice
        directive = sales_runtime_policy.humour_directive(
            state,
            context=context,
            theme=theme,
            language=language,
            seed=f"{state.conversation_id}|{state.visit_id}|{state.turn_count}",
        )
        if not directive.allowed or not directive.text or not directive.theme:
            return directive, state
        recorded, updated = sales_session_store.record_humour(
            state.conversation_id,
            state.visit_id,
            theme=directive.theme,
            text=directive.text,
            minimum_turn_gap=4,
        )
        if not recorded:
            return HumourDirective(reason="humour_budget_rejected_at_commit"), updated
        sales_telemetry.emit(
            "humour_used",
            conversation_id=updated.conversation_id,
            visit_id=updated.visit_id,
            stage=updated.stage,
            data={"theme": directive.theme},
        )
        return directive, updated

    @staticmethod
    def _acknowledgement(state: SalesSessionState, language: Language) -> str:
        role = state.explicit_facts.get("role")
        industry = state.explicit_facts.get("industry")
        if language == "en":
            if role and industry:
                return f"That is helpful: you work in {industry} and focus on {role}"
            if role:
                return f"That is helpful; your work focuses on {role}"
            if industry:
                return f"That gives me useful context about your work in {industry}"
            if state.pain_points:
                return "That is a practical problem to target"
            return "Understood"
        if role and industry:
            return f"了解，您在{industry}领域主要负责{role}"
        if role:
            return f"了解，您的工作重点是{role}"
        if industry:
            return f"了解，这让我能针对{industry}行业来推荐"
        if state.pain_points:
            return "了解，这个痛点很适合从具体流程入手"
        return "了解"

    @staticmethod
    def _value_sentence(material: _ReplyMaterial, language: Language) -> str:
        if material.approved_claims:
            values = material.approved_claims[:3]
            if language == "en":
                return "A relevant workflow can help with " + ", ".join(values)
            return "针对这个场景，可以重点处理" + "、".join(values)
        if material.pitch:
            return material.pitch
        return (
            "A useful Smart Office design starts from the real workflow rather than a generic chatbot demonstration"
            if language == "en"
            else "Smart Office 的价值应从真实办公流程出发，而不是只做通用聊天展示"
        )

    def _reply_plan(
        self,
        state: SalesSessionState,
        *,
        language: Language,
        goal: str,
        material: _ReplyMaterial,
        question: str | None,
        recommended_action: str | None,
        humour: HumourDirective | None = None,
        mode: str = "pure_sales",
    ) -> SalesReplyPlan:
        return SalesReplyPlan(
            conversation_id=state.conversation_id,
            visit_id=state.visit_id,
            language=language,
            reply_mode=mode,  # type: ignore[arg-type]
            sales_stage=state.stage,
            goal=goal,
            approved_claims=material.approved_claims,
            prohibited_claims=material.prohibited_claims,
            visitor_context=self._profile_context(state),
            capability_ids=material.capability_ids,
            suggested_question=question,
            recommended_action=recommended_action,
            humour=humour or HumourDirective(reason="not_selected"),
            maximum_sentences=4,
        )

    def _persist(self, state: SalesSessionState) -> tuple[SalesSessionState, bool]:
        flags = sales_runtime_policy.feature_flags()
        if not flags.profile_persistence_enabled:
            return state, False
        persisted = sales_profile_persistence.persist_if_consented(state)
        if not persisted:
            return state, False
        updated = sales_session_store.mark_profile_persisted(
            state.conversation_id,
            state.visit_id,
        )
        sales_telemetry.emit(
            "profile_updated",
            conversation_id=updated.conversation_id,
            visit_id=updated.visit_id,
            stage=updated.stage,
            data={"consented": True},
        )
        return updated, True

    def _response(
        self,
        *,
        handled: bool,
        reason: str,
        extraction: SalesProfileExtraction,
        state: SalesSessionState,
        plan: SalesReplyPlan | None = None,
        fallback_text: str = "",
        ui_action: str | None = None,
    ) -> SalesTurnResponse:
        state, persisted = self._persist(state)
        if plan is not None and plan.sales_stage != state.stage:
            plan = plan.model_copy(update={"sales_stage": state.stage})
        return SalesTurnResponse(
            handled=handled,
            route="sales_realtime" if handled else "pass_through",
            reason=reason,
            extraction=extraction,
            reply_plan=plan,
            fallback_text=fallback_text,
            ui_action=ui_action,  # type: ignore[arg-type]
            session=state,
            profile_persisted=persisted,
        )

    def handle_turn(self, request: SalesTurnRequest) -> SalesTurnResponse:
        flags = sales_runtime_policy.feature_flags()
        state = sales_session_store.get_or_create(
            request.conversation_id,
            request.visit_id,
            language=request.language,
        )
        extraction = sales_profile_extractor.extract(
            request.text,
            language=request.language,
            recent_context=request.recent_context,
        )
        if not flags.agent_enabled or request.actor_type != "visitor":
            return self._response(
                handled=False,
                reason="sales_runtime_disabled_or_nonvisitor_actor",
                extraction=extraction,
                state=state,
            )

        state = sales_session_store.record_user_turn(
            request.conversation_id,
            request.visit_id,
            effective=not extraction.direct_operational_command,
            language=request.language,
        )
        sales_telemetry.emit(
            "transcript_ready",
            conversation_id=state.conversation_id,
            visit_id=state.visit_id,
            stage=state.stage,
            data={"sales_relevant": extraction.sales_relevant},
        )

        for field in extraction.declined_fields:
            state = sales_session_store.decline_field(
                state.conversation_id,
                state.visit_id,
                field,
            )
        if (
            extraction.explicit_facts
            or extraction.pain_points
            or extraction.interested_capabilities
            or extraction.objections
        ):
            state = sales_session_store.apply_explicit_patch(
                state.conversation_id,
                state.visit_id,
                SalesSessionPatch(
                    explicit_facts=extraction.explicit_facts,
                    pain_points=extraction.pain_points,
                    interested_capabilities=extraction.interested_capabilities,
                    objections=extraction.objections,
                ),
            )

        if extraction.direct_operational_command:
            return self._response(
                handled=False,
                reason="direct_operational_command_must_reach_office_interpreter",
                extraction=extraction,
                state=state,
            )

        if state.stage == "close" and not extraction.sales_relevant:
            return self._response(
                handled=False,
                reason="closed_sales_visit_does_not_capture_general_conversation",
                extraction=extraction,
                state=state,
            )

        bundle = self._bundle()
        capability_ids = self._capability_ids(extraction, state, bundle)
        material = self._material(capability_ids, request.language)

        if extraction.disengaged:
            state = self._safe_transition(state, "close", "explicit_disengagement")
            closing = bundle.playbooks.disengagement.get(
                "closing_zh" if request.language == "zh" else "closing_en",
                "",
            )
            plan = self._reply_plan(
                state,
                language=request.language,
                goal="graceful_non_pressuring_close",
                material=material,
                question=None,
                recommended_action=None,
                mode="closing",
            )
            return self._response(
                handled=True,
                reason="explicit_disengagement",
                extraction=extraction,
                state=state,
                plan=plan,
                fallback_text=_clean(closing, 1_500),
            )

        if extraction.privacy_question:
            state = self._safe_transition(state, "handle_objection", "privacy_question")
            text = (
                "销售档案模块只会在您明确同意并提交登记后，保存联系方式以及本次对话中您明确表达的行业、职责、痛点和兴趣摘要。匿名销售状态会在 Visit 结束时删除；这个销售档案不保存原始音频、完整转写或人脸特征。"
                if request.language == "zh"
                else "The sales-profile module saves contact details and an explicit summary of industry, role, pain points and interests only after you consent and submit registration. Anonymous sales state is deleted when the Visit ends, and this profile does not store raw audio, full transcripts or face features."
            )
            plan = self._reply_plan(
                state,
                language=request.language,
                goal="answer_privacy_question_with_implemented_boundaries",
                material=material,
                question=None,
                recommended_action=None,
            )
            return self._response(
                handled=True,
                reason="privacy_question",
                extraction=extraction,
                state=state,
                plan=plan,
                fallback_text=text,
            )

        if extraction.booking_intent == "reject":
            state = sales_session_store.reject_booking(state.conversation_id, state.visit_id)
            text = (
                "好的，我不会再邀请您预约。您仍然可以继续了解任何感兴趣的功能。"
                if request.language == "zh"
                else "Understood. I will not ask you to book again, and you can still explore any capability that interests you."
            )
            plan = self._reply_plan(
                state,
                language=request.language,
                goal="respect_booking_rejection",
                material=material,
                question=None,
                recommended_action=None,
            )
            return self._response(
                handled=True,
                reason="booking_rejected",
                extraction=extraction,
                state=state,
                plan=plan,
                fallback_text=text,
            )

        if extraction.contact_intent == "reject":
            state = sales_session_store.reject_contact(state.conversation_id, state.visit_id)
            text = (
                "好的，我不会再询问联系方式。"
                if request.language == "zh"
                else "Understood. I will not ask for contact details again."
            )
            plan = self._reply_plan(
                state,
                language=request.language,
                goal="respect_contact_rejection",
                material=material,
                question=None,
                recommended_action=None,
            )
            return self._response(
                handled=True,
                reason="contact_rejected",
                extraction=extraction,
                state=state,
                plan=plan,
                fallback_text=text,
            )

        if extraction.booking_intent in {"accept", "direct"}:
            state = self._safe_transition(state, "convert", "booking_requested")
            state = sales_session_store.mark_booking_opened(state.conversation_id, state.visit_id)
            text = (
                "可以，我现在为您打开会议预约日历。请选择日期，再选择绿色的可用时间段。"
                if request.language == "zh"
                else "Certainly. I am opening the meeting-booking calendar now. Choose a date and then select a green available time slot."
            )
            plan = self._reply_plan(
                state,
                language=request.language,
                goal="open_booking_after_explicit_request",
                material=self._material(["meeting_booking"], request.language),
                question=None,
                recommended_action="open_booking",
            )
            sales_telemetry.emit(
                "booking_opened",
                conversation_id=state.conversation_id,
                visit_id=state.visit_id,
                stage=state.stage,
                data={},
            )
            return self._response(
                handled=True,
                reason="explicit_booking_request",
                extraction=extraction,
                state=state,
                plan=plan,
                fallback_text=text,
                ui_action="open_booking",
            )

        if extraction.contact_intent in {"accept", "direct"}:
            state = self._safe_transition(state, "convert", "contact_registration_requested")
            state = sales_session_store.mark_contact_opened(state.conversation_id, state.visit_id)
            text = (
                "可以，我现在打开登记信息表。只有您确认同意并提交后，联系方式才会保存。"
                if request.language == "zh"
                else "Certainly. I am opening visitor registration now. Contact details are saved only after you confirm consent and submit the form."
            )
            plan = self._reply_plan(
                state,
                language=request.language,
                goal="open_contact_registration_after_explicit_request",
                material=self._material(["visitor_registration"], request.language),
                question=None,
                recommended_action="open_contact",
            )
            return self._response(
                handled=True,
                reason="explicit_contact_registration_request",
                extraction=extraction,
                state=state,
                plan=plan,
                fallback_text=text,
                ui_action="open_contact",
            )

        if extraction.cost_question:
            state = self._safe_transition(state, "handle_objection", "cost_question")
            allowed, state = sales_session_store.record_cost_claim(
                state.conversation_id,
                state.visit_id,
            )
            claim = sales_runtime_policy.approved_cost_claim(
                language=request.language,
                seed=f"{state.conversation_id}|{state.visit_id}",
            ) if allowed else None
            if claim:
                analogy, scope = claim
                text = _sentence_join([analogy, scope], request.language, 2)
            else:
                text = (
                    "实际价格取决于模型和语音用量、硬件、部署方式、系统集成范围以及定制服务，因此需要结合企业场景评估。"
                    if request.language == "zh"
                    else "The actual price depends on model and voice usage, hardware, deployment, systems integration and custom services, so it must be assessed against the business scenario."
                )
            plan = self._reply_plan(
                state,
                language=request.language,
                goal="answer_cost_question_with_scope_limited_approved_claim",
                material=material,
                question=None,
                recommended_action="offer_scenario_discussion",
            )
            return self._response(
                handled=True,
                reason="cost_question",
                extraction=extraction,
                state=state,
                plan=plan,
                fallback_text=text,
            )

        if extraction.explicit_demo_request:
            if not extraction.demo_capability_id:
                state = self._safe_transition(state, "discover", "ambiguous_demo_request")
                question = (
                    "您更想看 PowerPoint、Teams、会议总结，还是办公自动化？"
                    if request.language == "zh"
                    else "Would you rather see PowerPoint, Teams, meeting summaries or office automation?"
                )
                plan = self._reply_plan(
                    state,
                    language=request.language,
                    goal="clarify_requested_demo_capability",
                    material=material,
                    question=question,
                    recommended_action=None,
                )
                return self._response(
                    handled=True,
                    reason="ambiguous_demo_request",
                    extraction=extraction,
                    state=state,
                    plan=plan,
                    fallback_text=question,
                )

            count, state = sales_session_store.increment_demo_request(
                state.conversation_id,
                state.visit_id,
                extraction.demo_capability_id,
            )
            decision = sales_runtime_policy.demonstration_decision(
                extraction.demo_capability_id,
                language=request.language,
                explicit_demo_request_count=count,
            )
            demo_material = self._material([extraction.demo_capability_id], request.language)
            if decision.action == "offer_booking":
                state = self._safe_transition(state, "recommend", "appointment_first_demo_offer")
                offered, state = sales_session_store.offer_booking(
                    state.conversation_id,
                    state.visit_id,
                )
                question = None
                if offered:
                    question = (
                        "要不要我现在帮您预约一次完整体验？"
                        if request.language == "zh"
                        else "Would you like me to book a complete experience now?"
                    )
                    sales_telemetry.emit(
                        "booking_offered",
                        conversation_id=state.conversation_id,
                        visit_id=state.visit_id,
                        stage=state.stage,
                        data={"capability_id": extraction.demo_capability_id},
                    )
                text = _sentence_join(
                    [decision.approved_pitch or demo_material.pitch, question or ""],
                    request.language,
                    3,
                )
                state = sales_session_store.mark_value_delivered(
                    state.conversation_id,
                    state.visit_id,
                    action="appointment_first_value_explained",
                )
                plan = self._reply_plan(
                    state,
                    language=request.language,
                    goal="explain_appointment_first_demo_value",
                    material=demo_material,
                    question=question,
                    recommended_action="offer_booking" if offered else None,
                )
                return self._response(
                    handled=True,
                    reason="first_demo_request_uses_appointment_first_policy",
                    extraction=extraction,
                    state=state,
                    plan=plan,
                    fallback_text=text,
                )

            if decision.onsite_allowed:
                state = self._safe_transition(state, "demonstrate", "allowlisted_onsite_demo")
                delegated_actions = {
                    "short_verified_presentation_demo": "delegate:请打开并演示 PowerPoint",
                    "teams_open_only": "delegate:打开 Teams",
                }
                delegated = delegated_actions.get(decision.action)
                if delegated:
                    plan = self._reply_plan(
                        state,
                        language=request.language,
                        goal="delegate_allowlisted_short_verified_demo",
                        material=demo_material,
                        question=None,
                        recommended_action=delegated,
                    )
                    return self._response(
                        handled=False,
                        reason="repeated_demo_request_delegated_to_office_interpreter",
                        extraction=extraction,
                        state=state,
                        plan=plan,
                    )
                text = (
                    "可以展示一个受控的局部流程，但这项现场操作还需要更具体的对象或参数。请告诉我您希望先看哪一个小步骤。"
                    if request.language == "zh"
                    else "A controlled part of the workflow can be shown here, but this action needs a more specific object or parameter. Tell me which small step you want to see first."
                )
                plan = self._reply_plan(
                    state,
                    language=request.language,
                    goal="clarify_allowlisted_short_demo",
                    material=demo_material,
                    question=(
                        "您希望先看哪一个小步骤？"
                        if request.language == "zh"
                        else "Which small step would you like to see first?"
                    ),
                    recommended_action=decision.action,
                )
                return self._response(
                    handled=True,
                    reason="repeated_demo_requires_bounded_parameters",
                    extraction=extraction,
                    state=state,
                    plan=plan,
                    fallback_text=text,
                )

        if not extraction.sales_relevant:
            return self._response(
                handled=False,
                reason="ordinary_conversation_without_sales_signal",
                extraction=extraction,
                state=state,
            )

        if extraction.objections or extraction.ordinary_chatbot_objection:
            state = self._safe_transition(state, "handle_objection", "sales_objection")
        elif capability_ids or state.pain_points:
            state = self._safe_transition(state, "recommend", "explicit_need_connected_to_solution")
        else:
            state = self._safe_transition(state, "discover", "explicit_profile_context_received")

        if extraction.ordinary_chatbot_objection:
            material = _ReplyMaterial(
                capability_ids=material.capability_ids,
                approved_claims=[
                    "不只生成回答" if request.language == "zh" else "It is not limited to generating answers",
                    "连接受控工具和企业流程" if request.language == "zh" else "It connects controlled tools and enterprise workflows",
                    "操作结果需要验证" if request.language == "zh" else "Operational results are verified",
                ],
                prohibited_claims=material.prohibited_claims,
                pitch=material.pitch,
            )

        question, state = self._next_discovery_question(state, request.language, bundle)
        humour, state = self._select_humour(
            state,
            extraction,
            material.capability_ids,
            request.language,
        )
        acknowledgement = self._acknowledgement(state, request.language)
        value = self._value_sentence(material, request.language)
        text_parts = [acknowledgement, value]
        if humour.allowed and humour.text:
            text_parts.append(humour.text)
        if question:
            text_parts.append(question)
        fallback = _sentence_join(text_parts, request.language, 4)
        state = sales_session_store.mark_value_delivered(
            state.conversation_id,
            state.visit_id,
        )
        plan = self._reply_plan(
            state,
            language=request.language,
            goal="connect_explicit_customer_context_to_approved_solution",
            material=material,
            question=question,
            recommended_action=None,
            humour=humour,
        )
        sales_telemetry.emit(
            "reply_plan_ready",
            conversation_id=state.conversation_id,
            visit_id=state.visit_id,
            stage=state.stage,
            data={
                "capability_count": len(material.capability_ids),
                "question_included": bool(question),
                "humour_included": humour.allowed,
            },
        )
        return self._response(
            handled=True,
            reason="explicit_sales_context_processed",
            extraction=extraction,
            state=state,
            plan=plan,
            fallback_text=fallback,
        )

    def plan_proactive(self, request: SalesProactiveRequest) -> SalesProactiveResponse:
        flags = sales_runtime_policy.feature_flags()
        state = sales_session_store.get_or_create(
            request.conversation_id,
            request.visit_id,
            language=request.language,
        )
        if not flags.agent_enabled or not flags.proactive_enabled:
            return SalesProactiveResponse(
                speak=False,
                reason="proactive_sales_disabled",
                session=state,
            )
        if request.silence_seconds < 7:
            return SalesProactiveResponse(
                speak=False,
                reason="silence_threshold_not_reached",
                session=state,
            )
        if not sales_runtime_policy.can_proactively_nudge(
            state,
            user_speaking=request.user_speaking,
            agent_speaking=request.agent_speaking,
            tool_active=request.tool_active,
            interaction_input_active=request.interaction_input_active,
        ):
            return SalesProactiveResponse(
                speak=False,
                reason="proactive_policy_gate_blocked",
                session=state,
            )

        allowed, state = sales_session_store.record_proactive_nudge(
            state.conversation_id,
            state.visit_id,
        )
        if not allowed:
            return SalesProactiveResponse(
                speak=False,
                reason="proactive_nudge_budget_exhausted",
                session=state,
            )

        bundle = self._bundle()
        material = self._material(state.interested_capabilities[:3], request.language)
        question: str | None = None
        recommended_action: str | None = None

        if state.proactive_nudge_count == 1:
            question, state = self._next_discovery_question(state, request.language, bundle)
            if not question:
                question = (
                    "您平时更想改善会议、客户跟进，还是重复行政工作？"
                    if request.language == "zh"
                    else "Would you most like to improve meetings, customer follow-up or repetitive administration?"
                )
            fallback = question
            goal = "low_pressure_first_silence_discovery"
        elif (
            state.value_delivered
            and bool(state.interested_capabilities or state.pain_points)
            and sales_runtime_policy.can_offer_booking(state)
        ):
            offered, state = sales_session_store.offer_booking(
                state.conversation_id,
                state.visit_id,
            )
            if offered:
                question = (
                    "我可以根据您刚才关注的场景安排一次完整体验，要现在打开预约日历吗？"
                    if request.language == "zh"
                    else "I can arrange a complete experience around the scenario you mentioned. Shall I open the booking calendar now?"
                )
                recommended_action = "offer_booking"
                fallback = question
                goal = "high_interest_second_silence_booking_offer"
                sales_telemetry.emit(
                    "booking_offered",
                    conversation_id=state.conversation_id,
                    visit_id=state.visit_id,
                    stage=state.stage,
                    data={"source": "proactive_second_nudge"},
                )
            else:
                fallback = (
                    "您可以继续自由体验；需要完整演示时再告诉我。"
                    if request.language == "zh"
                    else "Please continue exploring, and tell me whenever you would like a complete demonstration."
                )
                goal = "quiet_second_nudge_without_repeated_offer"
        else:
            fallback = (
                "如果您只是参观也没关系。我们致力于开发企业个人办公助理，也可以根据企业实际流程定制需求；欢迎有空到公司完整体验。"
                if request.language == "zh"
                else "It is completely fine if you are only looking around. We develop personal enterprise office assistants and adapt them to real workflows; you are welcome to visit our office for a complete experience."
            )
            goal = "low_interest_non_pressuring_second_nudge"

        plan = self._reply_plan(
            state,
            language=request.language,
            goal=goal,
            material=material,
            question=question,
            recommended_action=recommended_action,
            mode="proactive_sales",
        )
        sales_telemetry.emit(
            "proactive_nudge",
            conversation_id=state.conversation_id,
            visit_id=state.visit_id,
            stage=state.stage,
            data={"nudge_number": state.proactive_nudge_count},
        )
        return SalesProactiveResponse(
            speak=True,
            reason=goal,
            reply_plan=plan,
            fallback_text=fallback,
            session=state,
        )


sales_reply_planner = SalesReplyPlanner()
