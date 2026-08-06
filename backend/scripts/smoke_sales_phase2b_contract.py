from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.sales_phase2b import (  # noqa: E402
    Phase2BInteractionRequest,
    Phase2BObserveRequest,
    Phase2BOutputRequest,
    Phase2BProactiveRequest,
    sales_phase2b,
)
from app.sales_session_store import sales_session_store  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def observe(conversation_id: str, visit_id: str) -> None:
    first = sales_phase2b.observe_turn(
        Phase2BObserveRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language="zh",
            text="我是建筑行业的项目经理，最麻烦的是会后行动项整理。",
        )
    )
    require(first["engagement"]["current_industry"] == "construction", "Construction context was not detected.")
    require(first["observed"]["pain_point"] == "会后行动项整理", "Explicit pain point was not retained.")
    require("项目经理" in str(first["session"]["explicit_facts"].get("role") or ""), "Role-first context was not retained.")

    second = sales_phase2b.observe_turn(
        Phase2BObserveRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language="zh",
            text="我们开会后经常漏掉行动项，也需要跨团队协作。",
        )
    )
    session = second["session"]
    require(session["effective_user_turn_count"] == 2, "Effective user turns were not counted once per observed turn.")
    require("meeting_actions" in session["interested_capabilities"], "Meeting-action capability was not observed.")


def complete_output(
    conversation_id: str,
    visit_id: str,
    *,
    purpose: str,
    reply_mode: str,
    question_field: str | None = None,
) -> dict:
    return sales_phase2b.record_output(
        Phase2BOutputRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            result="completed",
            purpose=purpose,
            reply_mode=reply_mode,
            expect_user_response=question_field is not None,
            question_field=question_field,
            text=f"contract output for {purpose}",
        )
    )


def proactive(conversation_id: str, visit_id: str, **busy: bool):
    return sales_phase2b.plan_proactive(
        Phase2BProactiveRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language="zh",
            user_speaking=busy.get("user_speaking", False),
            agent_speaking=busy.get("agent_speaking", False),
            tool_active=busy.get("tool_active", False),
            interaction_input_active=busy.get("interaction_input_active", False),
        )
    )


def booking_flow() -> None:
    token = uuid4().hex[:10]
    conversation_id = f"phase2b-booking-{token}"
    visit_id = f"visit-booking-{token}"
    try:
        observe(conversation_id, visit_id)
        armed = complete_output(
            conversation_id,
            visit_id,
            purpose="general_voice_output",
            reply_mode="general",
        )
        require(armed["engagement"]["active"] is True, "A completed ordinary answer did not rearm engagement.")

        deferred = proactive(conversation_id, visit_id, tool_active=True)
        require(deferred.speak is False and deferred.defer is True, "Busy state was cancelled instead of deferred.")
        require(deferred.retry_after_ms == 2000, "Busy retry delay is not the configured two seconds.")
        require(deferred.engagement["episode_nudge_count"] == 0, "Busy deferral consumed a nudge budget.")

        first = proactive(conversation_id, visit_id)
        require(first.speak is True and first.reply is not None, "Contextual recommendation was not produced.")
        require(first.reason == "scenario_recommendation", "First follow-up was not a scenario recommendation.")
        require("会议结论" in first.reply.text, "Construction recommendation did not use the configured scenario.")
        require(first.reply.delivery.style in {"confident_recommendation", "light_playful"}, "Recommendation voice style is incorrect.")
        require(first.reply.question_field == "office_pain_points", "Recommendation did not end with one contextual discovery question.")
        require(first.session["value_delivered"] is True, "Scenario recommendation did not mark customer value delivered.")

        complete_output(
            conversation_id,
            visit_id,
            purpose="sales_phase2b_proactive_first",
            reply_mode="proactive_sales",
            question_field="office_pain_points",
        )
        second = proactive(conversation_id, visit_id)
        require(second.speak is True and second.reply is not None, "Second engagement step was not produced.")
        require(second.reason == "contextual_booking_offer", "Booking was not selected after value delivery.")
        require(second.reply.question_field == "booking", "Booking offer did not create the booking question field.")
        require(second.reply.recommended_action == "offer_booking", "Booking offer did not expose its controlled next action.")

        opened = sales_phase2b.record_interaction(
            Phase2BInteractionRequest(
                conversation_id=conversation_id,
                visit_id=visit_id,
                kind="meeting",
                result="opened",
                verified=True,
            )
        )
        require(opened["engagement"]["stage"] == "interaction_active", "Opening the calendar did not enter interaction state.")
        require(opened["engagement"]["conversion_state"] == "none", "Opening the calendar was incorrectly counted as conversion success.")

        submitted = sales_phase2b.record_interaction(
            Phase2BInteractionRequest(
                conversation_id=conversation_id,
                visit_id=visit_id,
                kind="meeting",
                result="submitted",
                verified=True,
                data={"booking_id": "contract-booking"},
            )
        )
        require(submitted["engagement"]["stage"] == "converted", "Verified booking submission did not close the conversion loop.")
        require(submitted["engagement"]["conversion_state"] == "booking_completed", "Verified booking state is incorrect.")
    finally:
        sales_phase2b.end_visit(conversation_id, visit_id)
        sales_session_store.end_visit(conversation_id, visit_id)


def contact_after_booking_rejection_flow() -> None:
    token = uuid4().hex[:10]
    conversation_id = f"phase2b-contact-{token}"
    visit_id = f"visit-contact-{token}"
    try:
        observe(conversation_id, visit_id)
        complete_output(
            conversation_id,
            visit_id,
            purpose="general_voice_output",
            reply_mode="general",
        )
        first = proactive(conversation_id, visit_id)
        require(first.reason == "scenario_recommendation", "Contact flow did not first deliver contextual value.")
        complete_output(
            conversation_id,
            visit_id,
            purpose="sales_phase2b_proactive_first",
            reply_mode="proactive_sales",
            question_field="office_pain_points",
        )
        sales_session_store.reject_booking(conversation_id, visit_id)
        second = proactive(conversation_id, visit_id)
        require(second.speak is True and second.reply is not None, "Contact follow-up was not produced.")
        require(second.reason == "contextual_contact_offer_after_booking_rejection", "Contact was not selected after booking rejection.")
        require(second.reply.question_field == "contact", "Contact offer did not create a contact pending intent.")
        require(second.reply.recommended_action == "offer_contact", "Contact offer did not expose its controlled next action.")

        opened = sales_phase2b.record_interaction(
            Phase2BInteractionRequest(
                conversation_id=conversation_id,
                visit_id=visit_id,
                kind="contact",
                result="opened",
                verified=True,
            )
        )
        require(opened["engagement"]["conversion_state"] == "none", "Opening registration was incorrectly counted as success.")
        submitted = sales_phase2b.record_interaction(
            Phase2BInteractionRequest(
                conversation_id=conversation_id,
                visit_id=visit_id,
                kind="contact",
                result="submitted",
                verified=True,
                data={"contact_id": "contract-contact"},
            )
        )
        require(submitted["engagement"]["conversion_state"] == "contact_completed", "Verified contact state is incorrect.")
        require(submitted["session"]["profile_persisted"] is True, "Verified consented contact did not mark the profile eligible for persistence.")
    finally:
        sales_phase2b.end_visit(conversation_id, visit_id)
        sales_session_store.end_visit(conversation_id, visit_id)


def main() -> int:
    self_test = sales_phase2b.self_test()
    require(self_test["ok"] is True, f"Phase 2B self-test failed: {self_test}")
    require(self_test["single_visit_owner"] is True, "Phase 2B is not declared as the single Visit engagement owner.")
    require(self_test["busy_policy"] == "defer_not_cancel", "Busy policy contract is incorrect.")
    require(self_test["conversion_verification"] == "submitted_and_verified", "Conversion verification contract is incorrect.")
    booking_flow()
    contact_after_booking_rejection_flow()
    print(
        "PASS: Phase 2B provides one Visit engagement owner, all-output rearming, "
        "busy deferral, construction-aware recommendations, bounded humour, booking-first "
        "conversion, role-aware contact fallback and verified form submission semantics."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
