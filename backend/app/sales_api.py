from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.sales_config import sales_config
from app.sales_models import (
    SalesProfileExtraction,
    SalesProactiveRequest,
    SalesProactiveResponse,
    SalesReplyPlan,
    SalesSessionState,
    SalesTurnRequest,
    SalesTurnResponse,
)
from app.sales_policy import sales_runtime_policy
from app.sales_profile_persistence import sales_profile_persistence
from app.sales_reply_planner import sales_reply_planner
from app.sales_session_store import sales_session_store
from app.sales_telemetry import sales_telemetry

router = APIRouter(prefix="/api/sales", tags=["sales-phase1"])


class SalesConversionEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    event: Literal[
        "booking_opened",
        "booking_failed",
        "contact_opened",
        "contact_failed",
    ]


class SalesVisitEndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)


def _require_configuration() -> None:
    if sales_config.load() is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Sales configuration is invalid.",
                "configuration": sales_config.status(),
            },
        )


def _sentences(text: str) -> list[str]:
    clean = " ".join(str(text or "").strip().split())
    if not clean:
        return []
    return [item.strip() for item in re.split(r"(?<=[。！？.!?])\s*", clean) if item.strip()]


def _compact_text(text: str, language: str, maximum_sentences: int = 2) -> str:
    pieces = _sentences(text)[:maximum_sentences]
    if not pieces:
        return ""
    joined = " ".join(pieces) if language == "en" else "".join(pieces)
    maximum = 280 if language == "en" else 150
    return joined[:maximum].rstrip()


def _compact_response(response: SalesTurnResponse) -> SalesTurnResponse:
    plan = response.reply_plan
    if plan is not None and plan.maximum_sentences != 2:
        plan = plan.model_copy(update={"maximum_sentences": 2})
    fallback = _compact_text(response.fallback_text, response.session.language, 2)
    return response.model_copy(update={"reply_plan": plan, "fallback_text": fallback})


def _first_value_sentence(text: str, language: str) -> str:
    pieces = _sentences(text)
    for piece in pieces:
        if not re.search(r"[？?]", piece):
            return piece
    return pieces[0] if pieces else (
        "Smart Office can be tailored to your actual work."
        if language == "en"
        else "Smart Office 可以直接结合您的实际工作。"
    )


def _role_question(language: str) -> str:
    return (
        "What kind of work are you mainly responsible for?"
        if language == "en"
        else "您主要从事什么工作？"
    )


def _contact_value(role: str, language: str) -> str:
    clean_role = " ".join(str(role or "").split())[:80]
    if language == "en":
        return (
            f"For {clean_role}, Smart Office can connect follow-up, meetings and repetitive office work into one practical workflow."
        )
    return f"哦，{clean_role}这类工作很适合把客户跟进、会议和重复办公流程串起来。"


def _maybe_offer_contact(response: SalesTurnResponse) -> SalesTurnResponse:
    """Implement the short exhibition funnel without touching Office routing.

    The opening asks for role. A sales turn that still lacks role asks only that one
    question. As soon as an explicit role is available, the same response gives one
    concise value sentence and the visit's single contact invitation. Explicit
    acceptance continues through the existing open_contact action.
    """

    plan = response.reply_plan
    state = response.session
    if (
        not response.handled
        or plan is None
        or response.reason != "explicit_sales_context_processed"
        or response.ui_action is not None
    ):
        return _compact_response(response)

    role = " ".join(str(state.explicit_facts.get("role") or "").split())[:80]
    if not role:
        question = _role_question(state.language)
        plan = plan.model_copy(
            update={
                "suggested_question": question,
                "recommended_action": None,
                "maximum_sentences": 2,
            }
        )
        fallback = _compact_text(
            f"{_first_value_sentence(response.fallback_text, state.language)} {question}",
            state.language,
            2,
        )
        return response.model_copy(
            update={"reply_plan": plan, "fallback_text": fallback}
        )

    if not sales_runtime_policy.can_offer_contact(state):
        return _compact_response(response)

    allowed, state = sales_session_store.offer_contact(
        state.conversation_id,
        state.visit_id,
    )
    if not allowed:
        return _compact_response(response)

    question = (
        "Shall I open visitor registration so a consultant can follow up on your work scenario?"
        if state.language == "en"
        else "需要我打开登记信息表，让顾问根据您的工作场景继续联系吗？"
    )
    plan = plan.model_copy(
        update={
            "suggested_question": question,
            "recommended_action": "offer_contact",
            "maximum_sentences": 2,
        }
    )
    sales_telemetry.emit(
        "contact_offered",
        conversation_id=state.conversation_id,
        visit_id=state.visit_id,
        stage=state.stage,
        data={"source": "fast_role_to_contact_funnel"},
    )
    return response.model_copy(
        update={
            "reply_plan": plan,
            "fallback_text": _compact_text(
                f"{_contact_value(role, state.language)} {question}",
                state.language,
                2,
            ),
            "session": state,
        }
    )


@router.get("/status")
def sales_status() -> dict:
    config_status = sales_config.status()
    policy_status = sales_runtime_policy.status()
    flags = policy_status["effective_flags"]
    runtime_active = bool(flags.get("agent_enabled", False))
    return {
        "ok": bool(config_status.get("ok")),
        "phase": "phase1_sales_runtime",
        "runtime_active": runtime_active,
        "current_conversation_behaviour_changed": runtime_active,
        "configuration": config_status,
        "policy": policy_status,
        "session_store": sales_session_store.status(),
        "telemetry": sales_telemetry.status(),
        "profile_persistence": sales_profile_persistence.status(),
        "contracts": {
            "sales_session": "sales-session-v1",
            "sales_reply_plan": "sales-reply-plan-v1",
            "sales_telemetry": "sales-telemetry-v1",
            "sales_profile_snapshot": "sales-profile-snapshot-v1",
        },
        "active_components": [
            "explicit_only_sales_profile_extraction",
            "sales_reply_generation",
            "proactive_sales_scheduler_api",
            "approved_humour_rendering",
            "appointment_first_runtime_routing",
            "consent_gated_sales_profile_persistence",
            "visit_scoped_invitation_limits",
            "allowlisted_repeated_demo_delegation",
            "single_post_value_contact_offer",
            "fast_role_to_contact_funnel",
            "two_sentence_sales_limit",
        ],
        "deferred_components": [
            "mini_model_ab_test",
            "gpt_live_api_adapter_after_public_release",
        ],
    }


@router.post("/turn", response_model=SalesTurnResponse)
def sales_turn(req: SalesTurnRequest) -> SalesTurnResponse:
    _require_configuration()
    try:
        # The exhibition UI deliberately uses operator for Office permissions. It
        # remains a visitor-facing sales conversation; only employee mode bypasses
        # sales discovery.
        if req.actor_type != "employee":
            req = req.model_copy(update={"actor_type": "visitor"})
        return _maybe_offer_contact(sales_reply_planner.handle_turn(req))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/proactive", response_model=SalesProactiveResponse)
def sales_proactive(req: SalesProactiveRequest) -> SalesProactiveResponse:
    _require_configuration()
    try:
        response = sales_reply_planner.plan_proactive(req)
        if response.reply_plan is not None and response.reply_plan.maximum_sentences != 2:
            response = response.model_copy(
                update={
                    "reply_plan": response.reply_plan.model_copy(
                        update={"maximum_sentences": 2}
                    ),
                    "fallback_text": _compact_text(
                        response.fallback_text,
                        response.session.language,
                        2,
                    ),
                }
            )
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/sessions/{conversation_id}/{visit_id}")
def sales_session(conversation_id: str, visit_id: str) -> dict:
    state = sales_session_store.snapshot(conversation_id, visit_id)
    return {
        "ok": state is not None,
        "phase": "phase1_sales_runtime",
        "session": state,
    }


@router.post("/conversion-event")
def sales_conversion_event(req: SalesConversionEventRequest) -> dict:
    state = sales_session_store.get_or_create(req.conversation_id, req.visit_id)
    if req.event == "booking_opened":
        state = sales_session_store.mark_booking_opened(req.conversation_id, req.visit_id)
        sales_telemetry.emit(
            "booking_opened",
            conversation_id=req.conversation_id,
            visit_id=req.visit_id,
            stage=state.stage,
            data={},
        )
    elif req.event == "contact_opened":
        state = sales_session_store.mark_contact_opened(req.conversation_id, req.visit_id)
    elif req.event == "booking_failed":
        state = sales_session_store.transition(
            req.conversation_id,
            req.visit_id,
            state.stage,
            action="booking_panel_failed",
        )
    elif req.event == "contact_failed":
        state = sales_session_store.transition(
            req.conversation_id,
            req.visit_id,
            state.stage,
            action="contact_panel_failed",
        )
    return {
        "ok": True,
        "phase": "phase1_sales_runtime",
        "event": req.event,
        "session": state,
    }


@router.post("/visit/end")
def sales_visit_end(req: SalesVisitEndRequest) -> dict:
    state = sales_session_store.snapshot(req.conversation_id, req.visit_id)
    persisted = False
    if state is not None and sales_runtime_policy.feature_flags().profile_persistence_enabled:
        persisted = sales_profile_persistence.persist_if_consented(state)
    ended = sales_session_store.end_visit(req.conversation_id, req.visit_id)
    sales_telemetry.emit(
        "visit_closed",
        conversation_id=req.conversation_id,
        visit_id=req.visit_id,
        stage=ended.stage if ended else None,
        data={"profile_persisted": persisted},
    )
    return {
        "ok": ended is not None,
        "phase": "phase1_sales_runtime",
        "profile_persisted": persisted,
        "anonymous_state_deleted": sales_session_store.snapshot(
            req.conversation_id,
            req.visit_id,
        ) is None,
    }


@router.get("/capabilities")
def sales_capabilities(
    language: Literal["zh", "en"] = Query(default="zh"),
) -> dict:
    bundle = sales_config.load()
    if bundle is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Sales configuration is invalid.",
                "configuration": sales_config.status(),
            },
        )
    return {
        "ok": True,
        "catalog_version": bundle.capabilities.catalog_version,
        "default_status": bundle.capabilities.default_status,
        "capabilities": [
            {
                "capability_id": item.capability_id,
                "title": item.title.model_dump().get(language),
                "status": item.status,
                "category": item.category,
                "live_actions": item.live_actions,
                "recommended_next_action": item.recommended_next_action,
                "onsite_fallback_after_repeated_request": (
                    item.onsite_fallback_after_repeated_request
                ),
                "approved_pitch": item.approved_pitch.model_dump().get(language),
                "prohibited_claims": item.prohibited_claims,
            }
            for item in bundle.capabilities.capabilities
        ],
    }


@router.get("/demonstration-policy/{capability_id}")
def demonstration_policy(
    capability_id: str,
    language: Literal["zh", "en"] = Query(default="zh"),
    explicit_request_count: int = Query(default=1, ge=1, le=10),
) -> dict:
    decision = sales_runtime_policy.demonstration_decision(
        capability_id,
        language=language,
        explicit_request_count=explicit_request_count,
    )
    return {
        "ok": decision.capability_status != "unknown",
        "decision": {
            "capability_id": decision.capability_id,
            "capability_status": decision.capability_status,
            "action": decision.action,
            "onsite_allowed": decision.onsite_allowed,
            "reason": decision.reason,
            "recommended_next_action": decision.recommended_next_action,
            "approved_pitch": decision.approved_pitch,
            "prohibited_claims": list(decision.prohibited_claims),
        },
    }


@router.get("/contracts")
def sales_contracts() -> dict:
    return {
        "ok": True,
        "phase": "phase1_sales_runtime",
        "sales_session_schema": SalesSessionState.model_json_schema(),
        "sales_reply_plan_schema": SalesReplyPlan.model_json_schema(),
        "sales_profile_extraction_schema": SalesProfileExtraction.model_json_schema(),
        "sales_turn_request_schema": SalesTurnRequest.model_json_schema(),
        "sales_turn_response_schema": SalesTurnResponse.model_json_schema(),
        "sales_proactive_request_schema": SalesProactiveRequest.model_json_schema(),
        "sales_proactive_response_schema": SalesProactiveResponse.model_json_schema(),
    }
