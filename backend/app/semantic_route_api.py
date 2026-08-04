from __future__ import annotations

import asyncio
import os
import time
import unicodedata
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.conversation_store import conversation_store
from app.sales_api import _maybe_offer_contact
from app.sales_models import SalesTurnRequest, SalesTurnResponse
from app.sales_reply_planner import sales_reply_planner
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
            "timeout_seconds", 12
        )
        return max(3.0, min(30.0, float(configured)))
    except (TypeError, ValueError):
        return 12.0


def _timeout_route(language: str) -> SemanticRoute:
    text = (
        "语义理解暂时超时。请用一句更明确的话告诉我是要执行操作，还是只想了解功能。"
        if language == "zh"
        else "Semantic understanding timed out. Please state in one clear sentence whether you want an action performed or only an explanation."
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
        reason_codes=["semantic_model_timeout_fail_closed"],
        source="safe_fallback",
        complexity="not_applicable",
        answer_engine="backend",
    )


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
    mode = semantic_route_policy.mode(
        os.getenv("SMART_OFFICE_SEMANTIC_ROUTER_MODE")
    )
    try:
        route, model, pending_used = await asyncio.wait_for(
            unified_semantic_router.classify(request),
            timeout=_semantic_timeout_seconds(),
        )
    except TimeoutError:
        route = _timeout_route(request.language)
        model = os.getenv("OPENAI_SEMANTIC_ROUTER_MODEL", "").strip() or None
        pending_used = None
    route = validate_semantic_action_evidence(route, request.text)
    route, final_decision, policy_reasons = semantic_route_policy.apply(route)
    legacy = _legacy_comparison(request) if mode in {"legacy", "shadow"} else None
    decision_id = unified_semantic_router.new_decision_id()
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))

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
        mode=mode,  # type: ignore[arg-type]
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
    """Run the existing deterministic sales policy with validated semantic facts.

    The semantic router supplies open-domain language understanding. The sales
    planner still owns invitation limits, capability claims, consent, stage changes
    and UI actions. Regex extraction remains only a fallback on the legacy endpoint.
    """

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
        "phase": "unified_semantic_routing",
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
        "pipeline": [
            "input_normalizer",
            "high_precision_fast_path",
            "structured_semantic_model",
            "action_evidence_validator",
            "profile_evidence_validator",
            "deterministic_policy_engine",
            "domain_planner",
            "executor_and_verifier",
            "response_renderer",
        ],
        "execution_rule": "No model-proposed tool name is accepted. Only evidence-backed, allowlisted structured actions may reach a domain executor.",
        "semantic_timeout_seconds": _semantic_timeout_seconds(),
    }


@router.post("/self-test")
async def semantic_route_self_test() -> dict[str, Any]:
    cases = [
        ("你是谁", "self_introduction", "answer_only"),
        ("打开 Teams", "application_action", "execute"),
        ("停止音乐", "system_action", "execute"),
        ("音量设置为30%", "system_action", "execute"),
        ("打开预约日历", "open_meeting_booking", "execute"),
    ]
    results: list[dict[str, Any]] = []
    for index, (text, expected_intent, expected_mode) in enumerate(cases):
        request = SemanticRouteRequest(
            conversation_id=f"semantic-self-test-{index}",
            visit_id=None,
            text=text,
            language="zh",
            actor_type="visitor",
        )
        route, _, _ = await unified_semantic_router.classify(request)
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
                "passed": passed,
            }
        )
    return {
        "ok": all(item["passed"] for item in results),
        "results": results,
    }
