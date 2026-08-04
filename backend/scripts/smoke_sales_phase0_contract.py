from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for name in (
    "SMART_OFFICE_SALES_AGENT_ENABLED",
    "SMART_OFFICE_SALES_PROACTIVE_ENABLED",
    "SMART_OFFICE_SALES_HUMOUR_ENABLED",
    "SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED",
):
    os.environ.pop(name, None)
os.environ["SMART_OFFICE_SALES_TELEMETRY_ENABLED"] = "true"
os.environ["SMART_OFFICE_REALTIME_MODE"] = "quality"

from app.main import app  # noqa: E402
from app.sales_config import sales_config  # noqa: E402
from app.sales_models import (  # noqa: E402
    SalesReplyPlan,
    SalesSessionPatch,
)
from app.sales_policy import sales_runtime_policy  # noqa: E402
from app.sales_session_store import SalesSessionStore  # noqa: E402
from app.sales_telemetry import SalesTelemetryStore  # noqa: E402


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def main() -> None:
    bundle = sales_config.reload()
    assert bundle is not None, sales_config.status()
    assert bundle.persona.identity.zh == "Smart Office 数字管理员与企业解决方案顾问"
    assert bundle.persona.conversion["maximum_booking_offers_per_visit"] == 2
    assert bundle.persona.conversion["maximum_contact_offers_per_visit"] == 1
    assert bundle.persona.demonstration["default_strategy"] == "appointment_first"
    assert bundle.persona.model_policy["quality_baseline"] == "gpt-realtime-2.1"

    capabilities = {
        item.capability_id: item for item in bundle.capabilities.capabilities
    }
    assert capabilities["teams_collaboration"].status == "appointment_demo"
    assert capabilities["presentation_automation"].status == "appointment_demo"
    assert capabilities["outlook_workflow"].status == "appointment_demo"
    assert capabilities["meeting_summary"].status == "appointment_demo"
    assert capabilities["activity_management"].status == "appointment_demo"
    assert capabilities["office_automation"].status == "appointment_demo"
    assert capabilities["product_planning"].status == "appointment_demo"
    assert capabilities["meeting_booking"].status == "live_demo"
    assert capabilities["meeting_booking"].category == "conversion"
    assert capabilities["visitor_registration"].status == "live_demo"
    assert capabilities["visitor_registration"].category == "conversion"
    appointment_first_count = sum(
        item.status == "appointment_demo"
        for item in bundle.capabilities.capabilities
    )
    assert appointment_first_count >= 7

    cost_claim = next(
        item
        for item in bundle.claims.claims
        if item.get("claim_id") == "typical_ai_voice_cost_analogy"
    )
    assert cost_claim["trigger_only_when_customer_asks"] is True
    assert cost_claim["maximum_uses_per_visit"] == 1
    assert "不包括硬件" in cost_claim["required_follow_up"]["zh"]
    assert len(bundle.claims.humour_themes) >= 20
    assert bundle.claims.global_humour_rules["minimum_turn_gap"] == 4

    flags = sales_runtime_policy.feature_flags()
    assert flags.agent_enabled is False
    assert flags.proactive_enabled is False
    assert flags.humour_enabled is False
    assert flags.profile_persistence_enabled is False
    assert flags.telemetry_enabled is True
    assert flags.realtime_mode == "quality"
    policy_status = sales_runtime_policy.status()
    assert policy_status["default_behaviour_unchanged"] is True

    suffix = uuid4().hex[:8]
    conversation_id = f"sales-phase0-{suffix}"
    visit_a = f"visit-a-{suffix}"
    visit_b = f"visit-b-{suffix}"
    store = SalesSessionStore()

    state_a = store.get_or_create(conversation_id, visit_a)
    state_b = store.get_or_create(conversation_id, visit_b)
    assert state_a.stage == "attract"
    assert state_b.explicit_facts == {}

    store.record_user_turn(conversation_id, visit_a)
    store.record_user_turn(conversation_id, visit_a)
    state_a = store.apply_explicit_patch(
        conversation_id,
        visit_a,
        SalesSessionPatch(
            explicit_facts={
                "industry": "construction",
                "role": "operations manager",
            },
            pain_points=["meeting follow-up"],
            interested_capabilities=["meeting_summary"],
        ),
    )
    assert state_a.explicit_facts["industry"] == "construction"
    assert state_a.pain_points == ["meeting follow-up"]
    assert store.snapshot(conversation_id, visit_b).explicit_facts == {}  # type: ignore[union-attr]

    asked, _ = store.mark_field_asked(conversation_id, visit_a, "company_type")
    assert asked is True
    asked_again, _ = store.mark_field_asked(conversation_id, visit_a, "company_type")
    assert asked_again is False
    declined = store.decline_field(conversation_id, visit_a, "company_type")
    assert "company_type" in declined.declined_fields
    declined_patch = store.apply_explicit_patch(
        conversation_id,
        visit_a,
        SalesSessionPatch(explicit_facts={"company_type": "large enterprise"}),
    )
    assert "company_type" not in declined_patch.explicit_facts

    first_booking, state_a = store.offer_booking(conversation_id, visit_a)
    assert first_booking is True
    immediate_second, _ = store.offer_booking(conversation_id, visit_a)
    assert immediate_second is False
    store.record_user_turn(conversation_id, visit_a)
    store.record_user_turn(conversation_id, visit_a)
    second_booking, state_a = store.offer_booking(conversation_id, visit_a)
    assert second_booking is True
    store.record_user_turn(conversation_id, visit_a)
    store.record_user_turn(conversation_id, visit_a)
    third_booking, state_a = store.offer_booking(conversation_id, visit_a)
    assert third_booking is False
    assert state_a.booking_offer_count == 2

    contact_allowed, state_a = store.offer_contact(conversation_id, visit_a)
    assert contact_allowed is True
    contact_again, state_a = store.offer_contact(conversation_id, visit_a)
    assert contact_again is False
    assert state_a.contact_offer_count == 1

    humour_allowed, state_a = store.record_humour(
        conversation_id,
        visit_a,
        theme="meeting_management",
        text="Meeting follow-up is where calendars develop a personality.",
    )
    assert humour_allowed is True
    repeated_humour, _ = store.record_humour(
        conversation_id,
        visit_a,
        theme="meeting_management",
        text="A different sentence with the same theme.",
    )
    assert repeated_humour is False
    for _ in range(4):
        store.record_user_turn(conversation_id, visit_a)
    second_theme, state_a = store.record_humour(
        conversation_id,
        visit_a,
        theme="document_organisation",
        text="No final_final_really_final filename was required.",
    )
    assert second_theme is True
    assert state_a.humour_used_count == 2

    nudge_one, _ = store.record_proactive_nudge(conversation_id, visit_a)
    nudge_two, _ = store.record_proactive_nudge(conversation_id, visit_a)
    nudge_three, state_a = store.record_proactive_nudge(conversation_id, visit_a)
    assert nudge_one is True
    assert nudge_two is True
    assert nudge_three is False
    assert state_a.proactive_nudge_count == 2

    state_a = store.transition(
        conversation_id,
        visit_a,
        "discover",
        action="explicit_need_discovered",
    )
    state_a = store.transition(
        conversation_id,
        visit_a,
        "recommend",
        action="meeting_summary_recommended",
    )
    assert state_a.stage == "recommend"

    first_teams = sales_runtime_policy.demonstration_decision(
        "teams_collaboration",
        explicit_demo_request_count=1,
    )
    assert first_teams.action == "offer_booking"
    assert first_teams.onsite_allowed is False
    assert "do_not_claim_a_teams_meeting_was_created" in first_teams.prohibited_claims

    repeated_teams = sales_runtime_policy.demonstration_decision(
        "teams_collaboration",
        explicit_demo_request_count=2,
    )
    assert repeated_teams.action == "teams_open_only"
    assert repeated_teams.onsite_allowed is True

    immediate_booking = sales_runtime_policy.demonstration_decision(
        "meeting_booking",
        explicit_demo_request_count=1,
    )
    assert immediate_booking.action == "open_booking"
    assert immediate_booking.onsite_allowed is True

    unknown = sales_runtime_policy.demonstration_decision("invented_capability")
    assert unknown.action == "do_not_claim_capability"
    assert unknown.onsite_allowed is False

    plan = sales_runtime_policy.phase0_reply_plan(
        state_a,
        language="zh",
        goal="connect_meeting_pain_point_to_approved_solution",
        capability_ids=["meeting_summary", "teams_collaboration"],
        suggested_question="您现在最耗时间的是会议前准备，还是会后整理？",
    )
    assert plan.reply_mode == "pure_sales"
    assert plan.sales_stage == "recommend"
    assert "never_claim_unverified_execution" in plan.prohibited_claims
    assert "meeting_summary" in plan.capability_ids
    assert plan.verified_facts == []

    try:
        SalesReplyPlan(
            conversation_id=conversation_id,
            visit_id=visit_a,
            reply_mode="exact_operational",
            sales_stage="demonstrate",
            goal="claim an operation",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Exact operational replies must require a verified fact.")

    telemetry = SalesTelemetryStore(maximum_events=100)
    event = telemetry.emit(
        "sales_context_ready",
        conversation_id=conversation_id,
        visit_id=visit_a,
        stage="recommend",
        data={
            "latency_ms": 18,
            "transcript": "must not be stored",
            "email": "must-not-be-stored@example.com",
        },
    )
    assert event is not None
    assert event.data == {"latency_ms": 18}
    assert telemetry.status()["stores_transcript"] is False
    assert telemetry.status()["stores_contact_details"] is False

    ended = store.end_visit(conversation_id, visit_a)
    assert ended is not None
    assert store.snapshot(conversation_id, visit_a) is None
    assert store.snapshot(conversation_id, visit_b) is not None

    client = TestClient(app)
    status_response = client.get("/api/sales/status")
    status_response.raise_for_status()
    status = status_response.json()
    assert status["phase"] == "phase0_sales_foundation"
    assert status["runtime_active"] is False
    assert status["current_conversation_behaviour_changed"] is False
    assert status["configuration"]["ok"] is True
    assert "sales_reply_generation" in status["phase1_not_yet_active"]

    capability_response = client.get("/api/sales/capabilities?language=zh")
    capability_response.raise_for_status()
    capability_payload = capability_response.json()
    assert capability_payload["default_status"] == "appointment_demo"
    teams_payload = next(
        item
        for item in capability_payload["capabilities"]
        if item["capability_id"] == "teams_collaboration"
    )
    assert teams_payload["status"] == "appointment_demo"

    policy_response = client.get(
        "/api/sales/demonstration-policy/teams_collaboration"
        "?language=zh&explicit_request_count=1"
    )
    policy_response.raise_for_status()
    assert policy_response.json()["decision"]["action"] == "offer_booking"

    repeated_policy_response = client.get(
        "/api/sales/demonstration-policy/teams_collaboration"
        "?language=zh&explicit_request_count=2"
    )
    repeated_policy_response.raise_for_status()
    repeated_decision = repeated_policy_response.json()["decision"]
    assert repeated_decision["action"] == "teams_open_only"
    assert repeated_decision["onsite_allowed"] is True

    contracts_response = client.get("/api/sales/contracts")
    contracts_response.raise_for_status()
    assert contracts_response.json()["sales_session_schema"]["title"] == "SalesSessionState"

    health_response = client.get("/")
    health_response.raise_for_status()
    health = health_response.json()
    assert health["capabilities"]["sales_phase0_foundation"] is True
    assert health["capabilities"]["sales_configuration_valid"] is True
    assert health["capabilities"]["sales_agent_enabled"] is False
    assert health["capabilities"]["sales_runtime_default_unchanged"] is True

    turn_api_source = read("backend/app/turn_api.py")
    general_chat_source = read("backend/app/general_chat_api.py")
    realtime_source = read("ui/smart-office-ui/src/voice/realtimeAgentRuntime.ts")
    assert "sales_runtime_policy" not in turn_api_source
    assert "sales_runtime_policy" not in general_chat_source
    assert "renderSalesReply" not in realtime_source

    print(
        "PASS: Phase 0 sales configuration, Visit isolation, invitation limits, "
        "declined-field fencing, appointment-first demonstration policy, humour "
        "budget, Reply Plan validation, privacy-bounded telemetry, status APIs and "
        "default-off runtime compatibility are present."
    )
    print(
        "NOTE: Phase 1 sales extraction, sales reply generation, proactive speech, "
        "humour rendering and profile persistence are intentionally not active."
    )


if __name__ == "__main__":
    main()
