from __future__ import annotations

import asyncio
import os
import time
import unicodedata
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.conversation_store import conversation_store
from app.routing_architecture import (
    attach_recent_context,
    hybrid_non_action_route,
    legacy_route,
    pending_conversion_route,
    routing_architecture,
)
from app.sales_api import _maybe_offer_contact
from app.sales_models import SalesTurnRequest, SalesTurnResponse
from app.sales_reply_planner import sales_reply_planner
from app.semantic_deterministic_router import classify_deterministic
from app.semantic_pending_store import semantic_pending_intents
from app.semantic_route_models import (
    PendingIntentRequest,
    RecentTurn,
    SemanticRoute,
    SemanticRouteRequest,
    SemanticRouteResponse,
)
from app.semantic_route_policy import semantic_route_policy
from app.semantic_route_validator import validate_semantic_action_evidence
from app.semantic_sales_bridge import use_semantic_sales_extraction
from app.turn_router import classify_turn
from app.unified_semantic_router import unified_semantic_router

router = APIRouter(prefix="/api/semantic-route", tags=["unified-semantic-routing"])


def _normalise_input_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(value.strip().split())


def _context_turns(request: SemanticRouteRequest) -> list[RecentTurn]:
    if request.recent_turns:
        return request.recent_turns[-12:]
    context = conversation_store.context_snapshot(
        request.conversation_id,
        language=request.language,
        actor_type=request.actor_type,
    )
    result: list[RecentTurn] = []
    messages = context.get("recent_messages")
    if isinstance(messages, list):
        for item in messages[-12:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            text = _normalise_input_text(str(item.get("text") or ""))
            if role not in {"user", "assistant", "system"} or not text:
                continue
            result.append(RecentTurn(role=role, text=text))
    return result


def _legacy_comparison(request: SemanticRouteRequest) -> dict[str, Any]:
    decision = classify_turn(request.text, request.actor_type)
    return {
        "route": decision.route,
        "scene": decision.scene,
        "reason": decision.reason,
    }


def _semantic_timeout_seconds() -> float:
    try:
        configured = unified_semantic_router.config().get("model", {}).get(
            "timeout_seconds", 4
        )
        return max(3.0, min(15.0, float(configured)))
    except (TypeError, ValueError):
        return 4.0


def _timeout_route(language: str) -> SemanticRoute:
    text = (
        "这条潜在操作指令需要更明确的目标或动作。请用一句话说明要我执行什么，或者说明您只是想了解功能。"
        if language == "zh"
        else "This possible action request needs a clearer target or action. Please state what to perform, or say that you only want an explanation."
    )
    return SemanticRoute(
        primary_intent="unknown",
        domain="unknown",
        action_mode="clarify",
        confidence=0.0,
        requires_clarification=True,
        clarification_question=text,
        entities={"language": language},
        risk="none",
        reason_codes=["semantic_model_timeout_fail_closed_for_action_candidate"],
        source="safe_fallback",
        complexity="not_applicable",
        answer_engine="backend",
    )


async def _model_route(
    request: SemanticRouteRequest,
) -> tuple[SemanticRoute, str | None, str | None]:
    try:
        return await asyncio.wait_for(
            unified_semantic_router.classify(request),
            timeout=_semantic_timeout_seconds(),
        )
    except TimeoutError:
        model = os.getenv("OPENAI_SEMANTIC_ROUTER_MODEL", "").strip() or None
        return _timeout_route(request.language), model, None


async def _classify_with_layers(
    request: SemanticRouteRequest,
) -> tuple[SemanticRoute, str | None, str | None, str]:
    architecture = routing_architecture()

    # Low-ambiguity commands, negations, explanations and identity questions retain
    # the deterministic fast path in every architecture mode.
    deterministic = classify_deterministic(request)
    if deterministic is not None:
        return deterministic, None, None, architecture

    # A one-turn booking/contact response is structured conversational state, not
    # an open-language classification problem. Preserve it in all modes.
    pending, pending_used = pending_conversion_route(request)
    if pending is not None:
        return pending, None, pending_used, architecture

    if architecture == "legacy":
        return legacy_route(request), None, None, architecture

    if architecture == "hybrid":
        # Ordinary non-action conversation fails open. It never waits for Terra and
        # never becomes an action-safety clarification because a model was slow or
        # returned invalid structured output.
        conversational = hybrid_non_action_route(request)
        if conversational is not None:
            return conversational, None, None, architecture

    route, model, pending_used = await _model_route(request)
    return route, model, pending_used, architecture


@router.post("", response_model=SemanticRouteResponse)
async def semantic_route(request: SemanticRouteRequest) -> SemanticRouteResponse:
    started = time.perf_counter()
    if request.visit_id and not conversation_store.is_current_visit(
        request.conversation_id, request.visit_id
    ):
        raise HTTPException(status_code=409, detail="stale_visit_before_semantic_route")

    normalised_text = _normalise_input_text(request.text)
    if not normalised_text:
        raise HTTPException(status_code=400, detail="empty_text_after_normalisation")
    request = request.model_copy(update={"text": normalised_text})
    request = request.model_copy(update={"recent_turns": _context_turns(request)})

    route, model, pending_used, architecture = await _classify_with_layers(request)
    route = attach_recent_context(route, request)
    route = validate_semantic_action_evidence(route, request.text)
    route, final_decision, policy_reasons = semantic_route_policy.apply(route)

    semantic_mode = semantic_route_policy.mode(
        os.getenv("SMART_OFFICE_SEMANTIC_ROUTER_MODE")
    )
    legacy = _legacy_comparison(request) if semantic_mode in {"legacy", "shadow"} else None
    decision_id = unified_semantic_router.new_decision_id()
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))

    route = route.model_copy(
        update={
            "entities": {
                **route.entities,
                "routing_architecture": architecture,
            },
            "reason_codes": [
                *route.reason_codes,
                f"routing_architecture:{architecture}",
            ][:20],
        }
    )

    unified_semantic_router.record(
        request=request,
        route=route,
        final_decision=final_decision,
        decision_id=decision_id,
        model=model,
        elapsed_ms=elapsed_ms,
        pending_intent_used=pending_used,
        legacy_comparison=legacy,
    )
    return SemanticRouteResponse(
        mode=semantic_mode,  # type: ignore[arg-type]
        route=route,
        final_policy_decision=final_decision,
        policy_reason_codes=policy_reasons,
        pending_intent_used=pending_used,
        legacy_comparison=legacy,
        decision_id=decision_id,
        model=model,
        elapsed_ms=elapsed_ms,
    )


@router.post("/sales-turn", response_model=SalesTurnResponse)
def semantic_sales_turn(request: SalesTurnRequest) -> SalesTurnResponse:
    extraction = request.semantic_extraction
    if extraction is None:
        raise HTTPException(status_code=400, detail="semantic_extraction_required")
    if not conversation_store.is_current_visit(
        request.conversation_id, request.visit_id
    ):
        raise HTTPException(status_code=409, detail="stale_visit_before_semantic_sales_turn")
    if request.actor_type != "employee":
        request = request.model_copy(update={"actor_type": "visitor"})
    with use_semantic_sales_extraction(extraction):
        return _maybe_offer_contact(sales_reply_planner.handle_turn(request))


@router.post("/pending")
def set_pending_intent(request: PendingIntentRequest) -> dict[str, Any]:
    if not conversation_store.is_current_visit(
        request.conversation_id, request.visit_id
    ):
        raise HTTPException(status_code=409, detail="stale_visit_before_pending_intent")
    value = semantic_pending_intents.set(
        request.conversation_id,
        request.visit_id,
        intent_type=request.intent_type,
        source_turn_id=request.source_turn_id,
        metadata=request.metadata,
        remaining_user_turns=1,
    )
    return {"ok": True, "pending": value.model_dump(mode="json")}


@router.delete("/pending/{conversation_id}/{visit_id}")
def clear_pending_intent(conversation_id: str, visit_id: str) -> dict[str, Any]:
    semantic_pending_intents.clear(conversation_id, visit_id)
    return {"ok": True}


@router.get("/status")
def semantic_route_status() -> dict[str, Any]:
    return {
        "ok": True,
        "phase": "phase2b_hybrid_routing_foundation",
        "routing_architecture": routing_architecture(),
        "available_architectures": ["legacy", "hybrid", "unified"],
        "mode": semantic_route_policy.mode(
            os.getenv("SMART_OFFICE_SEMANTIC_ROUTER_MODE")
        ),
        **unified_semantic_router.status(),
    }


@router.get("/recent-decisions")
def recent_semantic_decisions(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    return {
        "ok": True,
        "items": unified_semantic_router.recent()[:limit],
    }


@router.get("/contracts")
def semantic_route_contracts() -> dict[str, Any]:
    return {
        "ok": True,
        "route_schema": "semantic-route-v1",
        "configuration_schema": "semantic-router-config-v1",
        "routing_architecture": routing_architecture(),
        "available_architectures": ["legacy", "hybrid", "unified"],
        "pipeline": [
            "input_normalizer",
            "broad_deterministic_grammar",
            "structured_pending_conversion_intent",
            "hybrid_non_action_conversation_fail_open",
            "structured_terra_semantic_model_for_action_or_sales_candidates",
            "action_evidence_validator",
            "profile_evidence_validator",
            "deterministic_policy_engine",
            "domain_planner",
            "idempotent_executor_and_verifier",
            "response_renderer",
        ],
        "execution_rule": "No model-proposed tool name is accepted. Only evidence-backed, allowlisted structured actions may reach a domain executor.",
        "conversation_rule": "A non-action ordinary question is answerable even when the semantic model is unavailable.",
        "semantic_timeout_seconds": _semantic_timeout_seconds(),
    }


@router.post("/self-test")
async def semantic_route_self_test() -> dict[str, Any]:
    cases = [
        ("你是谁", "self_introduction", "answer_only"),
        ("你对建筑行业有什么了解", "general_question", "answer_only"),
        ("What do you know about the construction industry?", "general_question", "answer_only"),
        ("打开 Teams", "application_action", "execute"),
        ("请帮我启动微软团队", "application_action", "execute"),
        ("Could you open Teams?", "application_action", "execute"),
        ("先不要打开 Teams，介绍一下它能做什么", "capability_explanation", "answer_only"),
        ("Teams 为什么总是打不开", "capability_explanation", "answer_only"),
        ("如果打开 Teams 会发生什么", "general_question", "answer_only"),
        ("停止音乐", "system_action", "execute"),
        ("音量设置为30%", "system_action", "execute"),
        ("打开预约日历", "open_meeting_booking", "execute"),
    ]
    results: list[dict[str, Any]] = []
    for index, (text, expected_intent, expected_mode) in enumerate(cases):
        request = SemanticRouteRequest(
            conversation_id=f"semantic-self-test-{uuid4().hex[:8]}-{index}",
            visit_id=None,
            text=text,
            language="zh" if any("\u3400" <= char <= "\u9fff" for char in text) else "en",
            actor_type="visitor",
        )
        route, _, _, architecture = await _classify_with_layers(request)
        route = attach_recent_context(route, request)
        route = validate_semantic_action_evidence(route, request.text)
        route, final, _ = semantic_route_policy.apply(route)
        passed = route.primary_intent == expected_intent and final == expected_mode
        results.append(
            {
                "text": text,
                "expected_intent": expected_intent,
                "actual_intent": route.primary_intent,
                "expected_decision": expected_mode,
                "actual_decision": final,
                "source": route.source,
                "architecture": architecture,
                "passed": passed,
            }
        )
    return {
        "ok": all(item["passed"] for item in results),
        "routing_architecture": routing_architecture(),
        "results": results,
    }
