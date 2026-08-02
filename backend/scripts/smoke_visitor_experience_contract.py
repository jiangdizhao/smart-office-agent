from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ["SMART_OFFICE_ADMIN_PASSWORD"] = "contract-admin-password"
os.environ.pop("SMART_OFFICE_ADMIN_PASSWORD_HASH", None)
os.environ["SMART_OFFICE_TIMEZONE"] = "Australia/Sydney"
os.environ["SMART_OFFICE_DEMO_SCHEDULE_SEED"] = "visitor-experience-contract"
os.environ["SMART_OFFICE_DEMO_STAFF_FILE"] = str(
    REPO_ROOT / "config" / "demo_meeting_staff.json"
)

from app.main import app  # noqa: E402
from app.result_center_auth import reset_result_center_auth_for_tests  # noqa: E402


def auth_headers(token: str, visit_id: str, panel_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-SmartOffice-Visit-Id": visit_id,
        "X-SmartOffice-Panel-Instance-Id": panel_id,
    }


def login_admin(client: TestClient, visit_id: str, panel_id: str) -> str:
    response = client.post(
        "/api/result-center/admin/login",
        json={
            "password": "contract-admin-password",
            "visit_id": visit_id,
            "panel_instance_id": panel_id,
        },
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def create_contact(
    client: TestClient,
    *,
    conversation_id: str,
    visit_id: str,
    name: str,
    email: str,
) -> str:
    response = client.post(
        "/api/contact-records",
        json={
            "conversation_id": conversation_id,
            "visit_id": visit_id,
            "name": name,
            "company": "Smart Office Contract Company",
            "email": email,
            "phone": None,
            "interest_tags": ["PowerPoint 语音控制", "会议与录音"],
            "notes": "Visitor experience integration contract.",
            "contact_consent": True,
            "consent_statement_version": "expo-contact-summary-booking-v2",
            "source": "contract",
        },
    )
    response.raise_for_status()
    return str(response.json()["contact_id"])


def main() -> None:
    reset_result_center_auth_for_tests()
    with tempfile.TemporaryDirectory(prefix="smart-office-visitor-experience-") as temp_dir:
        os.environ["SMART_OFFICE_CONTACT_DB"] = str(Path(temp_dir) / "visitor_profiles.sqlite3")

        client = TestClient(app)
        conversation_a = "visitor-experience-conversation-a"
        visit_a = "visitor-experience-visit-a"
        panel_a = "visitor-experience-panel-a"
        conversation_b = "visitor-experience-conversation-b"
        visit_b = "visitor-experience-visit-b"

        # Current-session summaries are structured bullet points, not a verbatim chat log.
        summary_response = client.put(
            "/api/visitor-experience/session-summaries",
            json={
                "conversation_id": conversation_a,
                "visit_id": visit_a,
                "language": "zh",
                "status": "draft",
                "source_revision": 4,
                "messages": [
                    {
                        "role": "user",
                        "text": "我主要想了解 PowerPoint 语音控制，也想预约一次产品演示。",
                        "source": "contract",
                    },
                    {
                        "role": "assistant",
                        "text": "我已经打开会议预约日历，请选择绿色时间段。",
                        "source": "contract",
                    },
                    {
                        "role": "user",
                        "text": "之后请通过我的登记邮箱联系我。",
                        "source": "contract",
                    },
                ],
            },
        )
        summary_response.raise_for_status()
        summary = summary_response.json()["summary"]
        assert summary["status"] == "draft"
        assert summary["contact_id"] is None
        assert summary["bullet_points"]
        assert "PowerPoint 语音控制" in summary["interests"]
        assert any("后续" in item or "建议" in item for item in summary["bullet_points"])

        # Availability is deterministic for a date and seed, not re-randomised on refresh.
        selected_date = (
            datetime.now(ZoneInfo("Australia/Sydney")).date() + timedelta(days=7)
        ).isoformat()
        first_availability = client.get(
            "/api/visitor-experience/meeting-availability",
            params={"date": selected_date},
        )
        first_availability.raise_for_status()
        second_availability = client.get(
            "/api/visitor-experience/meeting-availability",
            params={"date": selected_date},
        )
        second_availability.raise_for_status()
        first_slots = first_availability.json()["slots"]
        second_slots = second_availability.json()["slots"]
        assert first_slots == second_slots
        available_slots = [item for item in first_slots if item["available"]]
        assert len(available_slots) >= 2
        selected_slot = available_slots[0]

        # Booking can happen before registration and is initially Visit-linked only.
        booking_response = client.post(
            "/api/visitor-experience/meeting-bookings",
            json={
                "conversation_id": conversation_a,
                "visit_id": visit_a,
                "date": selected_date,
                "slot_id": selected_slot["slot_id"],
                "topic": "Smart Office 产品演示",
            },
        )
        booking_response.raise_for_status()
        booking = booking_response.json()["booking"]
        assert booking_response.json()["contact_linked"] is False
        assert booking["status"] == "confirmed"
        assert booking["staff_name"]

        # A second visitor without an appointment provides the ordering control.
        contact_b = create_contact(
            client,
            conversation_id=conversation_b,
            visit_id=visit_b,
            name="No Booking Visitor",
            email="no-booking@example.com",
        )
        assert contact_b

        # Registration after booking automatically binds booking and summary to contact_id.
        contact_a = create_contact(
            client,
            conversation_id=conversation_a,
            visit_id=visit_a,
            name="Booked Visitor",
            email="booked@example.com",
        )
        assert contact_a

        token = login_admin(client, visit_a, panel_a)
        headers = auth_headers(token, visit_a, panel_a)

        unauthorised_profiles = client.get("/api/result-center/visitor-profiles")
        assert unauthorised_profiles.status_code == 401

        profiles_response = client.get(
            "/api/result-center/visitor-profiles",
            headers=headers,
        )
        profiles_response.raise_for_status()
        profiles = profiles_response.json()["profiles"]
        assert len(profiles) == 2
        assert profiles[0]["contact_id"] == contact_a
        assert profiles[0]["has_upcoming_appointment"] is True
        assert profiles[0]["appointment_count"] == 1
        assert profiles[0]["session_summary_count"] == 1
        assert profiles[1]["contact_id"] == contact_b
        assert profiles[1]["has_upcoming_appointment"] is False

        detail_response = client.get(
            f"/api/result-center/visitor-profiles/{contact_a}",
            headers=headers,
        )
        detail_response.raise_for_status()
        detail = detail_response.json()
        assert detail["contact"]["name"] == "Booked Visitor"
        assert len(detail["appointments"]) == 1
        assert detail["appointments"][0]["booking_id"] == booking["booking_id"]
        assert len(detail["session_summaries"]) == 1
        linked_summary = detail["session_summaries"][0]
        assert linked_summary["contact_id"] == contact_a
        assert linked_summary["bullet_points"]

        # The selected simulated slot becomes unavailable after booking.
        post_booking_availability = client.get(
            "/api/visitor-experience/meeting-availability",
            params={"date": selected_date},
        )
        post_booking_availability.raise_for_status()
        selected_after = next(
            item
            for item in post_booking_availability.json()["slots"]
            if item["slot_id"] == selected_slot["slot_id"]
        )
        assert selected_after["available"] is False

        frontend = (
            REPO_ROOT
            / "ui"
            / "smart-office-ui"
            / "src"
            / "interaction"
            / "VisitorExperienceApp.tsx"
        ).read_text(encoding="utf-8")
        summary_lifecycle = (
            REPO_ROOT
            / "ui"
            / "smart-office-ui"
            / "src"
            / "interaction"
            / "sessionSummaryLifecycle.ts"
        ).read_text(encoding="utf-8")
        rail = (
            REPO_ROOT
            / "ui"
            / "smart-office-ui"
            / "src"
            / "interaction"
            / "InteractionActionRail.tsx"
        ).read_text(encoding="utf-8")
        semantic = (
            REPO_ROOT
            / "ui"
            / "smart-office-ui"
            / "src"
            / "interaction"
            / "semanticInteractionInterpreter.ts"
        ).read_text(encoding="utf-8")

        assert "本 Session 要点" in frontend
        assert "绿色表示可预约" in frontend
        assert "确认预约" in frontend
        assert "has_upcoming_appointment" in frontend
        assert "Session 对话总结" in frontend
        assert "session-summary-updated" in summary_lifecycle
        assert "status: 'final'" in summary_lifecycle
        assert "disabled: true" not in rail
        assert "kind: 'meeting'" in rail
        assert '"intent":"contact|meeting|recording|transcript|results|none"' in semantic

        print(
            "PASS: deterministic simulated meeting availability, pre-registration booking, "
            "automatic contact linking, appointment-first profile sorting, Visit-scoped "
            "bullet summaries, and protected visitor-profile details are operational."
        )


if __name__ == "__main__":
    main()
