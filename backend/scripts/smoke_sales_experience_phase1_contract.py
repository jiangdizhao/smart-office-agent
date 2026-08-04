from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ["SMART_OFFICE_SALES_AGENT_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_PROACTIVE_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_HUMOUR_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED"] = "false"

from app.main import app  # noqa: E402
from app.sales_experience import (  # noqa: E402
    SalesExperienceProactiveRequest,
    sales_experience,
)
from app.sales_session_store import sales_session_store  # noqa: E402


def main() -> None:
    sales_session_store.clear()
    suffix = uuid4().hex[:10]
    conversation = f"experience-{suffix}"
    visit = f"visit-{suffix}"

    opening = sales_experience.plan_opening(
        conversation_id=conversation,
        visit_id=visit,
        language="zh",
        greeting_kind="new_anonymous",
    )
    assert opening.reply_mode == "opening"
    assert opening.purpose == "sales_opening"
    assert opening.expect_user_response is True
    assert opening.question_field == "industry"
    assert "数字管理员" in opening.text
    assert "企业解决方案顾问" in opening.text
    assert "虚拟接待员" not in opening.text
    assert "虚拟助手" not in opening.text
    assert "PowerPoint 语音控制、Outlook 助手" not in opening.text
    assert opening.humour_theme in {
        "digital_employee_intro",
        "office_resources",
        "multilingual",
    }
    assert opening.delivery.style in {"light_playful", "warm_confident"}
    assert opening.delivery.question_tone == "curious"
    assert opening.delivery.pause_before_question is True

    session = sales_session_store.snapshot(conversation, visit)
    assert session is not None
    assert "industry" in session.asked_fields
    assert session.humour_used_count == 1

    first = sales_experience.plan_proactive(
        SalesExperienceProactiveRequest(
            conversation_id=conversation,
            visit_id=visit,
            language="zh",
        )
    )
    assert first.speak is True
    assert first.reply is not None
    assert first.reply.purpose == "sales_proactive_first"
    assert first.reply.expect_user_response is True
    assert first.reply.question_field == "industry"
    assert "不方便说具体行业也没关系" in first.reply.text
    assert first.reply.delivery.style == "curious_discovery"

    second = sales_experience.plan_proactive(
        SalesExperienceProactiveRequest(
            conversation_id=conversation,
            visit_id=visit,
            language="zh",
        )
    )
    assert second.speak is True
    assert second.reply is not None
    assert second.reply.purpose == "sales_proactive_second"
    assert second.reply.expect_user_response is False
    assert second.reply.question_field is None
    assert "先自由参观" in second.reply.text
    assert second.reply.delivery.style == "calm_reassuring"

    third = sales_experience.plan_proactive(
        SalesExperienceProactiveRequest(
            conversation_id=conversation,
            visit_id=visit,
            language="zh",
        )
    )
    assert third.speak is False
    assert third.reason == "proactive_policy_gate_blocked"
    assert third.session.proactive_nudge_count == 2

    other_visit = f"other-{suffix}"
    other = sales_experience.plan_opening(
        conversation_id=conversation,
        visit_id=other_visit,
        language="en",
        greeting_kind="returning_anonymous",
    )
    assert other.visit_id == other_visit
    assert other.question_field == "interested_capabilities"
    assert sales_session_store.snapshot(conversation, other_visit) is not None
    assert sales_session_store.snapshot(conversation, visit) is not None

    registered = sales_experience.plan_opening(
        conversation_id=f"registered-{suffix}",
        visit_id=f"registered-visit-{suffix}",
        language="zh",
        greeting_kind="registered_identity",
        display_name="Rico",
    )
    assert "欢迎回来，Rico" in registered.text
    assert registered.humour_theme is None

    with TestClient(app) as client:
        self_test = client.get("/api/sales/experience/self-test")
        self_test.raise_for_status()
        payload = self_test.json()
        assert payload["ok"] is True
        assert payload["persona"]["zh"] == "Smart Office 数字管理员与企业解决方案顾问"
        assert payload["first_nudge_seconds"] == 7
        assert payload["second_delay_seconds"] == 8
        assert payload["maximum_nudges_per_visit"] == 2

        status = client.get(
            f"/api/sales/experience/status/{conversation}/{visit}"
        )
        status.raise_for_status()
        status_payload = status.json()
        assert status_payload["ok"] is True
        assert status_payload["experience"]["opening_completed"] is True
        assert status_payload["experience"]["pending_question_field"] == "industry"
        assert status_payload["sales_session"]["proactive_nudge_count"] == 2

    sales_experience.end_visit(conversation, visit)
    assert sales_experience.status(conversation, visit)["experience"] is None
    assert sales_session_store.snapshot(conversation, other_visit) is not None

    print(
        "PASS: Phase 1 customer experience uses the Digital Manager and Solution "
        "Consultant opening, approved opening humour, one pending discovery question, "
        "sequential 7s/8s proactive follow-ups, expressive delivery metadata, Visit "
        "isolation and a hard two-nudge limit."
    )


if __name__ == "__main__":
    main()
