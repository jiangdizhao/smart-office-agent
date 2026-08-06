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

# Phase 1 is active by default on the exhibition branch. This contract proves that
# an explicit disable restores the Phase 0 foundation and leaves Office paths open.
os.environ["SMART_OFFICE_SALES_AGENT_ENABLED"] = "false"
os.environ["SMART_OFFICE_SALES_PROACTIVE_ENABLED"] = "false"
os.environ["SMART_OFFICE_SALES_HUMOUR_ENABLED"] = "false"
os.environ["SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED"] = "false"
os.environ["SMART_OFFICE_SALES_TELEMETRY_ENABLED"] = "true"
os.environ["SMART_OFFICE_REALTIME_MODE"] = "quality"

from app.main import app  # noqa: E402
from app.sales_config import sales_config  # noqa: E402
from app.sales_models import SalesReplyPlan, SalesSessionPatch  # noqa: E402
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

    capabilities = {item.capability_id: item for item in bundle.capabilities.capabilities}
    for capability_id in (
        "teams_collaboration",
        "presentation_automation",
        "outlook_workflow",
        "meeting_summary",
        "activity_management",
        "office_automation",
        "product_planning",
    ):
        assert capabilities[capability_id].status == "appointment_demo"
    assert capabilities["meeting_booking"].status == "live_demo"
    assert capabilities["visitor_registration"].status == "live_demo"

    cost_claim = next(
        item
        for item in bundle.claims.claims
        if item.get("claim_id") == "typical_ai_voice_cost_analogy"
    )
    assert cost_claim["trigger_only_when_customer_asks"] is True
    assert cost_claim["maximum_uses_per_visit"] == 1
    assert "不包括硬件" in cost_claim["required_follow_up"]["zh"]
    assert len(bundle.claims.humour_themes) >= 20

    flags = sales_runtime_policy.feature_flags()
    assert flags.agent_enabled is False
    assert flags.proactive_enabled is False
    assert flags.humour_enabled is False
    assert flags.profile_persistence_enabled is False
    assert flags.telemetry_enabled is True
    assert flags.realtime_mode == "quality"
    policy_status = sales_runtime_policy.status()
    assert policy_status["runtime_active"] is False
    assert policy_status["default_behaviour_unchanged"] is True

    suffix = uuid4().hex[:8]
    conversation_id = f"sales-phase0-{suffix}"
    visit_a = f"visit-a-{suffix}"
    visit_b = f"visit-b-{suffix}"
    store = SalesSessionStore()

    store.record_user_turn(conversation_id, visit_a)
    store.record_user_turn(conversation_id, visit_a)
    state_a = store.apply_explicit_patch(
        conversation_id,
        visit_a,
        SalesSessionPatch(
            explicit_facts={"industry": "construction", "role": "operations manager"},
            pain_points=["meeting follow-up"],
            interested_capabilities=["meeting_summary"],
        ),
    )
    assert state_a.explicit_facts["industry"] == "construction"
    assert store.get_or_create(conversation_id, visit_b).explicit_facts == {}

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

    first_booking, _ = store.offer_booking(conversation_id, visit_a)
    assert first_booking is True
    immediate_second, _ = store.offer_booking(conversation_id, visit_a)
    assert immediate_second is False
    store.record_user_turn(conversation_id, visit_a)
    store.record_user_turn(conversation_id, visit_a)
    second_booking, state_a = store.offer_booking(conversation_id, visit_a)
    assert second_booking is True
    assert state_a.booking_offer_count == 2

    state_a = store.mark_value_delivered(conversation_id, visit_a)
    contact_allowed, _ = store.offer_contact(conversation_id, visit_a)
    assert contact_allowed is True
    contact_again, state_a = store.offer_contact(conversation_id, visit_a)
    assert contact_again is False
    assert state_a.contact_offer_count == 1

    humour_allowed, _ = store.record_humour(
        conversation_id,
        visit_a,
        theme="meeting_management",
        text="Approved configured humour.",
    )
    assert humour_allowed is True
    repeated_humour, _ = store.record_humour(
        conversation_id,
        visit_a,
        theme="meeting_management",
        text="Different text but the same theme.",
    )
    assert repeated_humour is False

    nudge_one, _ = store.record_proactive_nudge(conversation_id, visit_a)
    nudge_two, _ = store.record_proactive_nudge(conversation_id, visit_a)
    nudge_three, state_a = store.record_proactive_nudge(conversation_id, visit_a)
    assert (nudge_one, nudge_two, nudge_three) == (True, True, False)
    assert state_a.proactive_nudge_count == 2

    state_a = store.transition(conversation_id, visit_a, "discover")
    state_a = store.transition(conversation_id, visit_a, "recommend")
    first_teams = sales_runtime_policy.demonstration_decision(
        "teams_collaboration",
        explicit_demo_request_count=1,
    )
    repeated_teams = sales_runtime_policy.demonstration_decision(
        "teams_collaboration",
        explicit_demo_request_count=2,
    )
    assert first_teams.action == "offer_booking"
    assert first_teams.onsite_allowed is False
    assert repeated_teams.action == "teams_open_only"
    assert repeated_teams.onsite_allowed is True

    plan = sales_runtime_policy.phase0_reply_plan(
        state_a,
        language="zh",
        goal="compatibility_reply_plan",
        capability_ids=["meeting_summary"],
        suggested_question="您更关注哪个流程？",
    )
    assert plan.reply_mode == "pure_sales"
    assert "never_claim_unverified_execution" in plan.prohibited_claims

    try:
        SalesReplyPlan(
            conversation_id=conversation_id,
            visit_id=visit_a,
            reply_mode="exact_operational",
            sales_stage="demonstrate",
            goal="invalid unverified claim",
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

    ended = store.end_visit(conversation_id, visit_a)
    assert ended is not None
    assert store.snapshot(conversation_id, visit_a) is None
    assert store.snapshot(conversation_id, visit_b) is not None

    client = TestClient(app)
    status = client.get("/api/sales/status").json()
    assert status["phase"] == "phase1_sales_runtime"
    assert status["runtime_active"] is False
    assert status["current_conversation_behaviour_changed"] is False
    assert status["configuration"]["ok"] is True

    disabled_turn = client.post(
        "/api/sales/turn",
        json={
            "conversation_id": conversation_id,
            "visit_id": visit_b,
            "text": "我们做建筑行业，我负责运营",
            "language": "zh",
            "actor_type": "visitor",
            "recent_context": "",
        },
    )
    disabled_turn.raise_for_status()
    assert disabled_turn.json()["handled"] is False

    health = client.get("/").json()
    assert health["capabilities"]["sales_phase0_foundation"] is True
    assert health["capabilities"]["sales_configuration_valid"] is True
    assert health["capabilities"]["sales_agent_enabled"] is False
    assert health["capabilities"]["sales_runtime_default_unchanged"] is True

    turn_api_source = read("backend/app/turn_api.py")
    general_chat_source = read("backend/app/general_chat_api.py")
    realtime_source = read("ui/smart-office-ui/src/voice/realtimeAgentRuntime.ts")
    sales_facade = read("ui/smart-office-ui/src/voice/fastConversationRouter.ts")
    assert "sales_runtime_policy" not in turn_api_source
    assert "sales_runtime_policy" not in general_chat_source
    assert "renderSalesReply" not in realtime_source
    assert "salesConversationRouter" in sales_facade

    print(
        "PASS: Phase 0 configuration, Visit isolation, invitation limits, declined-field "
        "fencing, appointment-first policy, telemetry privacy and Office-path separation "
        "remain available when Phase 1 is explicitly disabled."
    )


if __name__ == "__main__":
    main()
