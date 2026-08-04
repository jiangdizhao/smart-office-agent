from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.sales_config import sales_config
from app.sales_models import SalesReplyPlan, SalesSessionState
from app.sales_policy import sales_runtime_policy
from app.sales_session_store import sales_session_store
from app.sales_telemetry import sales_telemetry

router = APIRouter(prefix="/api/sales", tags=["sales-phase0"])


@router.get("/status")
def sales_status() -> dict:
    config_status = sales_config.status()
    policy_status = sales_runtime_policy.status()
    return {
        "ok": bool(config_status.get("ok")),
        "phase": "phase0_sales_foundation",
        "runtime_active": bool(
            policy_status["effective_flags"].get("agent_enabled", False)
        ),
        "current_conversation_behaviour_changed": False,
        "configuration": config_status,
        "policy": policy_status,
        "session_store": sales_session_store.status(),
        "telemetry": sales_telemetry.status(),
        "contracts": {
            "sales_session": "sales-session-v1",
            "sales_reply_plan": "sales-reply-plan-v1",
            "sales_telemetry": "sales-telemetry-v1",
        },
        "phase1_not_yet_active": [
            "sales_profile_extraction",
            "sales_reply_generation",
            "proactive_sales_scheduler",
            "humour_rendering",
            "appointment_first_runtime_routing",
            "sales_profile_persistence",
        ],
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
        explicit_demo_request_count=explicit_request_count,
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
        "phase": "phase0_sales_foundation",
        "sales_session_schema": SalesSessionState.model_json_schema(),
        "sales_reply_plan_schema": SalesReplyPlan.model_json_schema(),
    }
