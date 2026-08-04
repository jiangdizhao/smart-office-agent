from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TEMP_DIR = tempfile.TemporaryDirectory(prefix="smart-office-sales-phase1-")
os.environ["SMART_OFFICE_CONTACT_DB"] = str(Path(_TEMP_DIR.name) / "contacts.sqlite3")
os.environ["SMART_OFFICE_SALES_AGENT_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_PROACTIVE_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_HUMOUR_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_TELEMETRY_ENABLED"] = "true"
os.environ["SMART_OFFICE_REALTIME_MODE"] = "quality"

from app.main import app  # noqa: E402
from app.sales_policy import sales_runtime_policy  # noqa: E402
from app.sales_session_store import sales_session_store  # noqa: E402


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def post_turn(
    client: TestClient,
    conversation_id: str,
    visit_id: str,
    text: str,
    *,
    recent_context: str = "",
) -> dict:
    response = client.post(
        "/api/sales/turn",
        json={
            "conversation_id": conversation_id,
            "visit_id": visit_id,
            "text": text,
            "language": "zh",
            "actor_type": "visitor",
            "recent_context": recent_context,
        },
    )
    response.raise_for_status()
    return response.json()


def conversion_event(
    client: TestClient,
    conversation_id: str,
    visit_id: str,
    event: str,
) -> dict:
    response = client.post(
        "/api/sales/conversion-event",
        json={
            "conversation_id": conversation_id,
            "visit_id": visit_id,
            "event": event,
        },
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    sales_session_store.clear()
    suffix = uuid4().hex[:10]

    with TestClient(app) as client:
        status = client.get("/api/sales/status")
        status.raise_for_status()
        payload = status.json()
        assert payload["phase"] == "phase1_sales_runtime"
        assert payload["runtime_active"] is True
        assert payload["policy"]["effective_flags"]["proactive_enabled"] is True
        assert payload["policy"]["effective_flags"]["humour_enabled"] is True
        assert payload["policy"]["effective_flags"]["realtime_mode"] == "quality"
        assert "explicit_only_sales_profile_extraction" in payload["active_components"]
        assert "single_post_value_contact_offer" in payload["active_components"]

        conversation = f"phase1-profile-{suffix}"
        visit = f"visit-profile-{suffix}"
        profile = post_turn(
            client,
            conversation,
            visit,
            "我们公司是做建筑行业，我负责运营管理，最大的问题是会议后没人跟进，也需要会议总结。",
        )
        assert profile["handled"] is True
        assert profile["route"] == "sales_realtime"
        assert profile["session"]["explicit_facts"]["industry"]
        assert "运营" in profile["session"]["explicit_facts"]["role"]
        assert profile["session"]["pain_points"]
        assert "meeting_summary" in profile["session"]["interested_capabilities"]
        # All four discovery targets were already explicit, so Sara must not ask a
        # redundant profile question merely to keep the funnel moving.
        assert profile["reply_plan"]["suggested_question"] is None
        assert profile["reply_plan"]["maximum_sentences"] <= 4
        assert profile["reply_plan"]["humour"]["allowed"] is True
        configured_humour = profile["reply_plan"]["humour"]["text"]
        assert configured_humour
        assert configured_humour in profile["fallback_text"]

        humour_repeat = post_turn(
            client,
            conversation,
            visit,
            "会议总结和会后跟进确实最浪费时间。",
        )
        assert humour_repeat["reply_plan"]["humour"]["allowed"] is False
        assert humour_repeat["session"]["humour_used_count"] == 1
        assert humour_repeat["session"]["contact_offer_count"] == 1
        assert humour_repeat["reply_plan"]["recommended_action"] == "offer_contact"
        assert "登记信息表" in humour_repeat["reply_plan"]["suggested_question"]

        contact_accept = post_turn(
            client,
            conversation,
            visit,
            "可以。",
            recent_context=humour_repeat["fallback_text"],
        )
        assert contact_accept["ui_action"] == "open_contact"
        assert contact_accept["session"]["contact_opened"] is False
        assert contact_accept["session"]["last_sales_action"] == "contact_panel_requested"
        contact_failed = conversion_event(
            client,
            conversation,
            visit,
            "contact_failed",
        )
        assert contact_failed["session"]["contact_opened"] is False
        assert contact_failed["session"]["last_sales_action"] == "contact_panel_failed"
        contact_opened = conversion_event(
            client,
            conversation,
            visit,
            "contact_opened",
        )
        assert contact_opened["session"]["contact_opened"] is True

        office_visit = f"visit-office-{suffix}"
        office = post_turn(
            client,
            f"phase1-office-{suffix}",
            office_visit,
            "打开 PowerPoint",
        )
        assert office["handled"] is False
        assert office["reason"] == "direct_operational_command_must_reach_office_interpreter"
        assert office["extraction"]["direct_operational_command"] is True

        booking_conversation = f"phase1-booking-{suffix}"
        booking_visit = f"visit-booking-{suffix}"
        booking_request = post_turn(
            client,
            booking_conversation,
            booking_visit,
            "我想预约一次完整体验。",
        )
        assert booking_request["ui_action"] == "open_booking"
        assert booking_request["session"]["booking_opened"] is False
        assert booking_request["session"]["last_sales_action"] == "booking_panel_requested"
        booking_failed = conversion_event(
            client,
            booking_conversation,
            booking_visit,
            "booking_failed",
        )
        assert booking_failed["session"]["booking_opened"] is False
        assert booking_failed["session"]["last_sales_action"] == "booking_panel_failed"
        booking_opened = conversion_event(
            client,
            booking_conversation,
            booking_visit,
            "booking_opened",
        )
        assert booking_opened["session"]["booking_opened"] is True

        demo_conversation = f"phase1-demo-{suffix}"
        demo_visit = f"visit-demo-{suffix}"
        first_demo = post_turn(
            client,
            demo_conversation,
            demo_visit,
            "请给我演示 PowerPoint。",
        )
        assert first_demo["handled"] is True
        assert first_demo["reason"] == "first_demo_request_uses_appointment_first_policy"
        assert first_demo["session"]["booking_offer_count"] == 1
        assert first_demo["reply_plan"]["recommended_action"] == "offer_booking"
        assert first_demo["reply_plan"]["suggested_question"] is not None

        second_demo = post_turn(
            client,
            demo_conversation,
            demo_visit,
            "我还是希望现在演示 PowerPoint。",
            recent_context=first_demo["fallback_text"],
        )
        assert second_demo["handled"] is False
        assert second_demo["reason"] == "repeated_demo_request_delegated_to_office_interpreter"
        assert second_demo["reply_plan"]["recommended_action"].startswith("delegate:")
        assert "PowerPoint" in second_demo["reply_plan"]["recommended_action"]
        assert second_demo["session"]["explicit_demo_request_counts"]["presentation_automation"] == 2

        rejected = post_turn(
            client,
            f"phase1-reject-{suffix}",
            f"visit-reject-{suffix}",
            "不用预约，我暂时不需要。",
            recent_context="要不要我现在帮您预约一次完整体验？",
        )
        assert rejected["handled"] is True
        assert rejected["reason"] == "booking_rejected"
        assert rejected["session"]["booking_rejected"] is True
        assert rejected["session"]["stage"] != "close"
        assert rejected["reply_plan"]["suggested_question"] is None

        cost_conversation = f"phase1-cost-{suffix}"
        cost_visit = f"visit-cost-{suffix}"
        cost_first = post_turn(
            client,
            cost_conversation,
            cost_visit,
            "这个系统价格大概是多少？",
        )
        assert cost_first["handled"] is True
        assert cost_first["session"]["cost_claim_used_count"] == 1
        assert "不包括硬件、部署、系统集成和定制服务" in cost_first["fallback_text"]
        cost_second = post_turn(
            client,
            cost_conversation,
            cost_visit,
            "再说一次成本。",
        )
        assert cost_second["session"]["cost_claim_used_count"] == 1
        assert "实际价格取决于" in cost_second["fallback_text"]

        privacy = post_turn(
            client,
            f"phase1-privacy-{suffix}",
            f"visit-privacy-{suffix}",
            "你们会保存我的隐私、录音或者人脸信息吗？",
        )
        assert privacy["handled"] is True
        assert privacy["reply_plan"]["humour"]["allowed"] is False
        assert "不保存原始音频、完整转写或人脸特征" in privacy["fallback_text"]
        assert privacy["reply_plan"]["suggested_question"] is None

        proactive_conversation = f"phase1-proactive-{suffix}"
        proactive_visit = f"visit-proactive-{suffix}"
        blocked = client.post(
            "/api/sales/proactive",
            json={
                "conversation_id": proactive_conversation,
                "visit_id": proactive_visit,
                "language": "zh",
                "silence_seconds": 7,
                "user_speaking": True,
                "agent_speaking": False,
                "tool_active": False,
                "interaction_input_active": False,
            },
        ).json()
        assert blocked["speak"] is False
        assert blocked["session"]["proactive_nudge_count"] == 0

        first_nudge = client.post(
            "/api/sales/proactive",
            json={
                "conversation_id": proactive_conversation,
                "visit_id": proactive_visit,
                "language": "zh",
                "silence_seconds": 7,
                "user_speaking": False,
                "agent_speaking": False,
                "tool_active": False,
                "interaction_input_active": False,
            },
        ).json()
        assert first_nudge["speak"] is True
        assert first_nudge["session"]["proactive_nudge_count"] == 1
        assert first_nudge["reply_plan"]["reply_mode"] == "proactive_sales"

        second_nudge = client.post(
            "/api/sales/proactive",
            json={
                "conversation_id": proactive_conversation,
                "visit_id": proactive_visit,
                "language": "zh",
                "silence_seconds": 15,
                "user_speaking": False,
                "agent_speaking": False,
                "tool_active": False,
                "interaction_input_active": False,
            },
        ).json()
        assert second_nudge["speak"] is True
        assert second_nudge["session"]["proactive_nudge_count"] == 2

        third_nudge = client.post(
            "/api/sales/proactive",
            json={
                "conversation_id": proactive_conversation,
                "visit_id": proactive_visit,
                "language": "zh",
                "silence_seconds": 30,
                "user_speaking": False,
                "agent_speaking": False,
                "tool_active": False,
                "interaction_input_active": False,
            },
        ).json()
        assert third_nudge["speak"] is False
        assert third_nudge["session"]["proactive_nudge_count"] == 2

        persist_conversation = f"phase1-persist-{suffix}"
        persist_visit = f"visit-persist-{suffix}"
        before_consent = post_turn(
            client,
            persist_conversation,
            persist_visit,
            "我们在教育行业，我负责行政，最大的问题是会议整理很麻烦。",
        )
        assert before_consent["profile_persisted"] is False

        contact = client.post(
            "/api/contact-records",
            json={
                "conversation_id": persist_conversation,
                "visit_id": persist_visit,
                "name": "Phase One Visitor",
                "company": "Example Education",
                "email": "phase1@example.com",
                "phone": None,
                "interest_tags": ["meeting_summary"],
                "notes": None,
                "contact_consent": True,
                "consent_statement_version": "expo-contact-consent-v1",
                "source": "phase1_contract",
            },
        )
        contact.raise_for_status()

        after_consent = post_turn(
            client,
            persist_conversation,
            persist_visit,
            "我也关注会议总结。",
        )
        assert after_consent["profile_persisted"] is True
        assert after_consent["session"]["profile_persisted"] is True

        with sqlite3.connect(os.environ["SMART_OFFICE_CONTACT_DB"]) as connection:
            snapshot = connection.execute(
                "SELECT explicit_facts_json, pain_points_json FROM sales_profile_snapshots WHERE visit_id = ?",
                (persist_visit,),
            ).fetchone()
        assert snapshot is not None
        assert "教育" in snapshot[0]
        assert "会议整理很麻烦" in snapshot[1]

        ended = client.post(
            "/api/sales/visit/end",
            json={
                "conversation_id": persist_conversation,
                "visit_id": persist_visit,
            },
        )
        ended.raise_for_status()
        assert ended.json()["anonymous_state_deleted"] is True
        assert sales_session_store.snapshot(persist_conversation, persist_visit) is None

        other_visit = sales_session_store.snapshot(conversation, visit)
        assert other_visit is not None
        assert other_visit.explicit_facts

    assert sales_runtime_policy.feature_flags().agent_enabled is True

    router_source = read("ui/smart-office-ui/src/sales/salesConversationRouter.ts")
    renderer_source = read("ui/smart-office-ui/src/sales/salesReplyRenderer.ts")
    scheduler_source = read("ui/smart-office-ui/src/sales/salesProactiveScheduler.ts")
    delegate_source = read("ui/smart-office-ui/src/sales/salesOfficeDelegate.ts")
    facade_source = read("ui/smart-office-ui/src/voice/fastConversationRouter.ts")
    assert "actor: 'visitor'" in router_source
    assert "queueSalesOfficeDelegate" in router_source
    assert "humour.text exactly once" in renderer_source
    assert "FIRST_NUDGE_MS = 7_000" in scheduler_source
    assert "SECOND_NUDGE_MS = 15_000" in scheduler_source
    assert "endSalesVisit" in scheduler_source
    assert "DELEGATE_TTL_MS = 8_000" in delegate_source
    assert "salesConversationRouter" in facade_source

    print(
        "PASS: Phase 1 performs explicit-only discovery, preserves Office commands, "
        "enforces appointment-first and repeated-demo delegation, verifies conversion "
        "panel outcomes, bounds cost claims and humour, runs two Visit-scoped proactive "
        "nudges, offers contact once after value, persists only after contact consent, "
        "and deletes anonymous Visit state."
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        _TEMP_DIR.cleanup()
