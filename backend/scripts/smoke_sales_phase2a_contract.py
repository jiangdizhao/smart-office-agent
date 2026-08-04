from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ["SMART_OFFICE_SALES_AGENT_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_PROACTIVE_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_HUMOUR_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED"] = "true"
os.environ["SMART_OFFICE_SALES_TELEMETRY_ENABLED"] = "true"
os.environ["SMART_OFFICE_REALTIME_MODE"] = "quality"

from app.sales_models import SalesSessionPatch  # noqa: E402
from app.sales_phase2a import (  # noqa: E402
    Phase2AOutputResultRequest,
    Phase2AProactiveRequest,
    sales_phase2a,
)
from app.sales_session_store import sales_session_store  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    conversation_id = "phase2a-contract-conversation"
    visit_id = "phase2a-contract-visit"
    sales_phase2a.reset_for_test(conversation_id, visit_id)

    self_test = sales_phase2a.self_test()
    require(self_test["ok"], f"Phase 2A self-test failed: {self_test}")
    require(self_test["limits"]["maximum_nudges_per_episode"] == 2, "Episode budget must be two.")
    require(self_test["limits"]["maximum_nudges_per_visit"] == 6, "Visit budget must be six.")

    zh_intro = sales_phase2a.self_introduction("zh")
    en_intro = sales_phase2a.self_introduction("en")
    for obsolete in ("Office 助手", "虚拟接待员", "Smart Office 虚拟助手", "企业个人办公助理"):
        require(obsolete.casefold() not in zh_intro.casefold(), f"Obsolete Chinese identity leaked: {obsolete}")
    for obsolete in ("office assistant", "virtual receptionist", "virtual host", "personal enterprise office assistant"):
        require(obsolete.casefold() not in en_intro.casefold(), f"Obsolete English identity leaked: {obsolete}")
    require("数字管理员与企业解决方案顾问" in zh_intro, "Canonical Chinese identity is missing.")
    require("Digital Manager and Enterprise Solution Consultant" in en_intro, "Canonical English identity is missing.")

    sales_session_store.get_or_create(conversation_id, visit_id, language="zh")
    sales_session_store.record_user_turn(conversation_id, visit_id, language="zh")
    sales_session_store.apply_explicit_patch(
        conversation_id,
        visit_id,
        SalesSessionPatch(
            explicit_facts={"industry": "建筑", "role": "项目经理"},
            pain_points=["会议后的行动项整理"],
            interested_capabilities=["meeting_summary"],
        ),
    )
    sales_session_store.transition(
        conversation_id,
        visit_id,
        "recommend",
        action="phase2a_contract_recommendation",
    )
    sales_session_store.mark_value_delivered(conversation_id, visit_id)

    continuity = sales_phase2a.record_output(
        Phase2AOutputResultRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            result="completed",
            purpose="sales_reply",
            reply_mode="pure_sales",
            expect_user_response=False,
            question_field=None,
            text="针对项目经理的会后流程，可以把摘要、行动项和跟进连接起来。",
        )
    )
    require(continuity.active, "A no-question sales reply must arm continuity.")
    require(continuity.episode_nudge_count == 0, "A new episode must start at zero nudges.")

    first = sales_phase2a.plan_proactive(
        Phase2AProactiveRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language="zh",
        )
    )
    require(first.speak and first.reply is not None, "First Phase 2A proactive output must speak.")
    require(first.reply.purpose == "sales_phase2a_proactive_first", "First purpose is incorrect.")
    require("会议后的行动项整理" in first.reply.text, "First nudge must use explicit customer context.")

    sales_phase2a.record_output(
        Phase2AOutputResultRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            result="completed",
            purpose=first.reply.purpose,
            reply_mode=first.reply.reply_mode,
            expect_user_response=first.reply.expect_user_response,
            question_field=first.reply.question_field,
            text=first.reply.text,
        )
    )
    second = sales_phase2a.plan_proactive(
        Phase2AProactiveRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language="zh",
        )
    )
    require(second.speak and second.reply is not None, "Second Phase 2A proactive output must speak.")
    require(second.reply.purpose == "sales_phase2a_proactive_second", "Second purpose is incorrect.")

    combined = f"{first.reply.text} {second.reply.text}".casefold()
    for forbidden in sales_phase2a.config()["forbidden_technology_terms"]:
        require(str(forbidden).casefold() not in combined, f"Specific technology leaked: {forbidden}")

    sales_phase2a.record_output(
        Phase2AOutputResultRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            result="completed",
            purpose=second.reply.purpose,
            reply_mode=second.reply.reply_mode,
            expect_user_response=second.reply.expect_user_response,
            question_field=second.reply.question_field,
            text=second.reply.text,
        )
    )
    third = sales_phase2a.plan_proactive(
        Phase2AProactiveRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language="zh",
        )
    )
    require(not third.speak, "A Phase 2A episode must never produce a third nudge.")

    sales_session_store.record_user_turn(conversation_id, visit_id, language="zh")
    restarted = sales_phase2a.record_output(
        Phase2AOutputResultRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            result="completed",
            purpose="sales_reply",
            reply_mode="pure_sales",
            expect_user_response=False,
            text="这个方案可以进一步按您的真实流程细化。",
        )
    )
    require(restarted.active, "A later sales reply must start a fresh episode.")
    require(restarted.episode_nudge_count == 0, "Fresh episode count must reset.")
    require(restarted.total_nudge_count == 2, "Visit total must not reset between episodes.")

    busy = sales_phase2a.plan_proactive(
        Phase2AProactiveRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language="zh",
            user_speaking=True,
        )
    )
    require(not busy.speak and busy.reason == "phase2a_busy_gate_blocked", "User speech must block proactive output.")

    print(
        "PASS: Phase 2A uses the canonical Sara identity, arms continuity after no-question sales replies, "
        "produces at most two contextual nudges per episode, preserves the Visit-wide budget, respects busy gates, "
        "and does not expose implementation technology."
    )


if __name__ == "__main__":
    main()
