from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ["SMART_OFFICE_SEMANTIC_ROUTER_MODE"] = "unified"

from app.semantic_pending_store import semantic_pending_intents  # noqa: E402
from app.semantic_route_models import (  # noqa: E402
    SemanticAction,
    SemanticEvidence,
    SemanticProfileExtraction,
    SemanticRoute,
    SemanticRouteRequest,
)
from app.semantic_route_policy import semantic_route_policy  # noqa: E402
from app.semantic_route_validator import validate_semantic_action_evidence  # noqa: E402
from app.unified_semantic_router import unified_semantic_router  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def fast_path_contract() -> None:
    cases = [
        ("你是谁", "self_introduction", "answer_only"),
        ("打开 Teams", "application_action", "execute"),
        ("停止音乐", "system_action", "execute"),
        ("音量设置为30%", "system_action", "execute"),
        ("打开预约日历", "open_meeting_booking", "execute"),
        ("下一页", "presentation_action", "execute"),
    ]
    for index, (text, intent, decision) in enumerate(cases):
        request = SemanticRouteRequest(
            conversation_id=f"semantic-fast-{index}",
            visit_id=None,
            text=text,
            language="zh",
        )
        route, model, pending = await unified_semantic_router.classify(request)
        route = validate_semantic_action_evidence(route, request.text)
        route, final, _ = semantic_route_policy.apply(route)
        require(model is None, f"Fast path unexpectedly used a model for: {text}")
        require(pending is None, f"Fast path unexpectedly used pending intent: {text}")
        require(route.source == "fast_path", f"Expected fast path for: {text}")
        require(route.primary_intent == intent, f"Wrong intent for {text}: {route.primary_intent}")
        require(final == decision, f"Wrong policy decision for {text}: {final}")


async def pending_intent_contract() -> None:
    conversation_id = "semantic-pending-conversation"
    visit_id = "semantic-pending-visit"
    semantic_pending_intents.clear(conversation_id, visit_id)
    semantic_pending_intents.set(
        conversation_id,
        visit_id,
        intent_type="booking_offer",
        source_turn_id="assistant-1",
    )
    route, _, used = await unified_semantic_router.classify(
        SemanticRouteRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            text="好的",
            language="zh",
        )
    )
    require(used == "booking_offer", "Structured booking pending intent was not used.")
    require(route.primary_intent == "booking_response", "Affirmation did not resolve booking response.")
    require(route.profile_extraction.booking_intent == "accept", "Booking acceptance was not structured.")
    require(
        semantic_pending_intents.peek(conversation_id, visit_id) is None,
        "Pending intent must be consumed after one user turn.",
    )


def policy_boundary_contract() -> None:
    discussion = SemanticRoute(
        primary_intent="capability_explanation",
        domain="general",
        action_mode="answer_only",
        confidence=0.98,
        actions=[],
        negated_actions=[
            SemanticAction(
                verb="open",
                target="teams",
                polarity="negated",
                speech_act="command",
                evidence="不要打开 Teams",
            )
        ],
        entities={"language": "zh", "application": "teams"},
        risk="none",
        reason_codes=["explicit_negation", "explanation_request"],
        source="semantic_model",
        complexity="simple",
        answer_engine="realtime",
    )
    checked = validate_semantic_action_evidence(
        discussion,
        "不要打开 Teams，先介绍一下它能做什么。",
    )
    checked, final, _ = semantic_route_policy.apply(checked)
    require(final == "answer_only", "Negated discussion must remain answer-only.")
    require(not checked.actions, "Negated action must never become executable.")

    hypothetical = SemanticRoute(
        primary_intent="application_action",
        domain="office",
        action_mode="execute",
        confidence=0.99,
        actions=[
            SemanticAction(
                verb="open",
                target="teams",
                polarity="affirmed",
                speech_act="hypothetical",
                evidence="如果打开 Teams",
            )
        ],
        entities={"language": "zh"},
        risk="low",
        reason_codes=["hypothetical"],
        source="semantic_model",
        answer_engine="office_interpreter",
    )
    checked = validate_semantic_action_evidence(
        hypothetical,
        "如果打开 Teams 会发生什么？",
    )
    checked, final, reasons = semantic_route_policy.apply(checked)
    require(final == "clarify", f"Hypothetical action must not execute: {final}, {reasons}")
    require(not checked.actions, "Hypothetical action must be removed by policy.")

    hallucinated = SemanticRoute(
        primary_intent="application_action",
        domain="office",
        action_mode="execute",
        confidence=0.99,
        actions=[
            SemanticAction(
                verb="open",
                target="teams",
                polarity="affirmed",
                speech_act="command",
                evidence="打开 Teams",
            )
        ],
        entities={"language": "zh"},
        risk="low",
        reason_codes=[],
        source="semantic_model",
        answer_engine="office_interpreter",
    )
    checked = validate_semantic_action_evidence(
        hallucinated,
        "介绍一下 Teams 能做什么。",
    )
    checked, final, reasons = semantic_route_policy.apply(checked)
    require(not checked.actions, "Action without source evidence survived validation.")
    require(final == "clarify", f"Hallucinated action must fail closed: {final}, {reasons}")
    require(
        any(code.startswith("action_evidence_rejected") for code in checked.reason_codes),
        "Rejected action did not leave a diagnostic reason code.",
    )

    external = SemanticRoute(
        primary_intent="email_action",
        domain="office",
        action_mode="execute",
        confidence=1.0,
        actions=[
            SemanticAction(
                verb="send",
                target="email_send",
                polarity="affirmed",
                speech_act="command",
                evidence="发送邮件",
            )
        ],
        entities={"language": "zh"},
        risk="none",
        reason_codes=[],
        source="semantic_model",
        answer_engine="office_interpreter",
    )
    checked = validate_semantic_action_evidence(external, "发送邮件")
    checked, final, _ = semantic_route_policy.apply(checked)
    require(final != "execute", "External-effect email action must never directly execute.")
    require(
        checked.risk == "none" or final in {"clarify", "request_confirmation"},
        "External action policy failed closed.",
    )


def evidence_contract() -> None:
    text = "我在建筑行业担任项目经理，最麻烦的是会后行动项整理。"
    route = SemanticRoute(
        primary_intent="provide_profile_context",
        domain="sales",
        action_mode="delegate",
        confidence=0.95,
        entities={"language": "zh"},
        risk="none",
        reason_codes=[],
        profile_extraction=SemanticProfileExtraction(
            explicit_facts={
                "industry": SemanticEvidence(value="建筑", evidence="建筑行业"),
                "role": SemanticEvidence(value="项目经理", evidence="项目经理"),
                "budget": SemanticEvidence(value="高预算", evidence="高预算"),
            },
            pain_points=[
                SemanticEvidence(value="会后行动项整理", evidence="会后行动项整理"),
                SemanticEvidence(value="不存在的痛点", evidence="不存在的痛点"),
            ],
            sales_relevant=True,
        ),
        source="semantic_model",
        answer_engine="backend",
    )
    checked = unified_semantic_router._validate_profile_evidence(route, text)
    require(
        set(checked.profile_extraction.explicit_facts) == {"industry", "role"},
        "Forbidden or unsupported fields survived validation.",
    )
    require(
        len(checked.profile_extraction.pain_points) == 1,
        "Unsupported evidence survived validation.",
    )


def source_architecture_contract() -> None:
    frontend = (
        ROOT / "ui" / "smart-office-ui" / "src" / "voice" / "fastConversationRouterCore.ts"
    ).read_text(encoding="utf-8")
    sales_router = (
        ROOT / "ui" / "smart-office-ui" / "src" / "sales" / "salesConversationRouter.ts"
    ).read_text(encoding="utf-8")
    sales_renderer = (
        ROOT / "ui" / "smart-office-ui" / "src" / "sales" / "salesReplyRenderer.ts"
    ).read_text(encoding="utf-8")
    sales_scheduler = (
        ROOT / "ui" / "smart-office-ui" / "src" / "sales" / "salesPhase2AProactiveScheduler.ts"
    ).read_text(encoding="utf-8")
    semantic_client = (
        ROOT / "ui" / "smart-office-ui" / "src" / "routing" / "unifiedSemanticRouterClient.ts"
    ).read_text(encoding="utf-8")
    semantic_api = (ROOT / "backend" / "app" / "semantic_route_api.py").read_text(
        encoding="utf-8"
    )
    require("requestUnifiedSemanticRoute" in frontend, "Frontend is not using unified semantic routing.")
    require("matchInteractionWindowIntent" not in frontend, "Legacy interaction matcher still owns the main route.")
    require("matchSystemAction" not in frontend, "Legacy system matcher still owns the main route.")
    require("canonicalSystemCommand" in frontend, "Execution adapter does not use canonical commands.")
    require("matchesSelfIntroduction" not in sales_router, "Identity regex still owns self-introduction routing.")
    require("semantic_decision" in sales_router, "Sales router is not consuming the unified decision.")
    require("setSemanticPendingIntent" not in sales_router, "Sales preview must not write pending intent before speech completes.")
    require("questionFieldForPlan" in sales_renderer, "Sales renderer does not label conversion questions.")
    require("recommended_action === 'offer_booking'" in sales_renderer, "Booking question lifecycle metadata is missing.")
    require("recommended_action === 'offer_contact'" in sales_renderer, "Contact question lifecycle metadata is missing.")
    require("syncPendingIntentAfterCompletedOutput" in sales_scheduler, "Completed-output pending lifecycle is missing.")
    require("void this.syncPendingIntentAfterCompletedOutput(detail)" in sales_scheduler, "Pending lifecycle is not bound to output completion.")
    require("detail.questionField === 'booking'" in sales_scheduler, "Completed booking question is not structured.")
    require("detail.questionField === 'contact'" in sales_scheduler, "Completed contact question is not structured.")
    require(
        "SUPPORTED_SALES_PROFILE_FIELDS" in semantic_client,
        "Frontend does not filter semantic facts to the deterministic sales schema.",
    )
    require(
        "validate_semantic_action_evidence(route, request.text)" in semantic_api,
        "API does not validate action evidence before policy execution.",
    )
    require("asyncio.wait_for" in semantic_api, "Semantic routing has no dedicated timeout.")
    require("semantic_model_timeout_fail_closed" in semantic_api, "Semantic timeout does not fail closed.")
    require("_normalise_input_text" in semantic_api, "Semantic input normalization is missing.")


async def main() -> None:
    await fast_path_contract()
    await pending_intent_contract()
    policy_boundary_contract()
    evidence_contract()
    source_architecture_contract()
    print(
        "PASS: unified semantic routing uses a narrow fast path, schema-validated model decisions, "
        "action and profile evidence validation, completion-bound pending intent and deterministic policy-gated execution."
    )


if __name__ == "__main__":
    asyncio.run(main())
