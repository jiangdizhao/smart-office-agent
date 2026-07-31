from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def main() -> None:
    client = TestClient(app)
    conversation_id = "conversation-proximity-contract"

    initial = client.get(
        f"/api/conversations/{conversation_id}",
        params={"language": "en", "actor_type": "visitor"},
    )
    initial.raise_for_status()
    assert initial.json()["state"]["conversation_phase"] == "standby"

    detection = {
        "language": "en",
        "actor_type": "visitor",
        "body_area_ratio": 0.31,
        "body_confidence": 0.91,
        "face_area_ratio": 0.04,
        "face_confidence": 0.94,
        "face_inside_body": True,
        "confidence": 0.91,
        "frontal_score": 1.0,
        "center_x": 0.5,
        "center_y": 0.5,
        "stable_frames": 4,
        "detector": "contract-person+face",
        "track_id": 7,
        "visitor_session_id": "visitor_new",
        "session_stable": True,
        "session_age_seconds": 5.2,
        "session_recovery_count": 0,
        "returning_visitor": False,
        "greeting_kind": "new_anonymous",
    }
    greeting = client.post(
        f"/api/conversations/{conversation_id}/proximity-greeting",
        json=detection,
    )
    greeting.raise_for_status()
    greeting_payload = greeting.json()
    assert greeting_payload["triggered"] is True
    assert greeting_payload["greeting"] == "Welcome to our office."
    assert greeting_payload["conversation_phase"] == "standby"

    # The Backend completes each visual attention cycle immediately. The active
    # client source owns visitor-session or absence rearm and prevents duplicates.
    duplicate = client.post(
        f"/api/conversations/{conversation_id}/proximity-greeting",
        json={**detection, "stable_frames": 5},
    )
    duplicate.raise_for_status()
    duplicate_payload = duplicate.json()
    assert duplicate_payload["triggered"] is True
    assert duplicate_payload["greeting"] == "Welcome to our office."
    assert duplicate_payload["conversation_phase"] == "standby"

    returning = client.post(
        "/api/conversations/conversation-returning-anonymous/proximity-greeting",
        json={
            **detection,
            "visitor_session_id": "visitor_returning",
            "session_recovery_count": 1,
            "returning_visitor": True,
            "greeting_kind": "returning_anonymous",
        },
    )
    returning.raise_for_status()
    assert returning.json()["greeting"] == "Welcome back."

    registered = client.post(
        "/api/conversations/conversation-registered/proximity-greeting",
        json={
            **detection,
            "visitor_session_id": "visitor_rico",
            "returning_visitor": True,
            "greeting_kind": "registered_identity",
            "identity_id": "person_rico",
            "display_name": "Rico",
            "identity_similarity": 0.81,
        },
    )
    registered.raise_for_status()
    assert registered.json()["greeting"] == "Welcome back, Rico."

    started = client.post(
        f"/api/conversations/{conversation_id}/turn-start",
        json={
            "language": "en",
            "actor_type": "visitor",
            "text": "What can you help me with?",
            "source": "text",
        },
    )
    started.raise_for_status()
    assert started.json()["conversation_phase"] == "engaged"

    completed = client.post(
        f"/api/conversations/{conversation_id}/turn-complete",
        json={
            "text": "I can introduce approved company information and assist employees with controlled Office tasks.",
            "route": "realtime_direct",
            "expect_reply": True,
            "source": "contract",
        },
    )
    completed.raise_for_status()
    assert completed.json()["conversation_phase"] == "awaiting_user"

    context = client.get(
        f"/api/conversations/{conversation_id}",
        params={"language": "en", "actor_type": "visitor"},
    )
    context.raise_for_status()
    state = context.json()["state"]
    assert state["conversation_phase"] == "awaiting_user"
    assert [message["role"] for message in state["recent_messages"]] == [
        "assistant",
        "assistant",
        "user",
        "assistant",
    ]
    assert "What can you help me with?" in state["conversation_summary"]

    controller = read("ui/smart-office-ui/src/voice/useOfficeVoiceController.ts")
    host = read("ui/smart-office-ui/src/virtual-host/VirtualHostApp.tsx")
    avatar = read("ui/smart-office-ui/src/virtual-host/VirtualHostAvatar.tsx")
    detector = read("ui/smart-office-ui/src/vision/proximityFaceMonitor.ts")
    remote_client = read("ui/smart-office-ui/src/vision/remoteVisionClient.ts")
    proximity_hook = read("ui/smart-office-ui/src/vision/useProximityGreeting.ts")
    drawer = read("ui/smart-office-ui/src/virtual-host/OperatorDrawer.tsx")

    for needle in (
        "conversationPhase",
        "turn-start",
        "turn-complete",
        "triggerProximityGreeting",
        "proximity-greeting",
    ):
        assert needle in controller, f"Missing controller contract: {needle}"

    for needle in (
        "conversation-${controller.conversationPhase}",
        "等待您继续",
        "useProximityGreeting",
    ):
        assert needle in host, f"Missing host state contract: {needle}"

    # Browser MediaPipe remains available as an explicit fallback.
    for needle in (
        "body_area_ratio",
        "face_inside_body",
        "ObjectDetector",
        "categoryAllowlist: ['person']",
        "VITE_PROXIMITY_BODY_AREA_RATIO",
        "REQUIRED_STABLE_FRAMES",
        "suppressUntilAbsent",
        "mediapipe-person+face",
    ):
        assert needle in detector, f"Missing MediaPipe fallback contract: {needle}"

    for needle in (
        "RemoteVisionClient",
        "client_state_snapshot",
        "get_client_state",
        "visitor_session_id",
        "session_stable",
        "returning_visitor",
        "greeting_kind",
        "greeting_eligible",
    ):
        assert needle in remote_client, f"Missing remote vision client contract: {needle}"

    for needle in (
        "smartoffice:host-intro-start",
        "Welcome to our office.",
        "Welcome back",
        "VITE_VISION_SOURCE",
        "greetedVisitRef",
        "REMOTE_REARM_ABSENCE_MS",
    ):
        assert needle in proximity_hook, f"Missing Phase 5.2 greeting trigger contract: {needle}"

    for needle in (
        "idle-primary.mp4",
        "idle-rare.mp4",
        "talk-a.mp4",
        "talk-b.mp4",
        "talk-c.mp4",
        "PRIMARY_IDLE_LOOPS_PER_RARE_GESTURE = 5",
        "CROSSFADE_MS = 260",
    ):
        assert needle in avatar, f"Missing segmented video avatar contract: {needle}"

    assert "空闲访客主动问候" in drawer
    assert "RTX 视觉服务器" in drawer
    assert "body_area_ratio" in drawer

    print("PASS: conversation memory, personalized Phase 5.2 greetings, fallback, and video-avatar contracts are present.")
    print("NOTE: Real LAN, RTX vision, camera geometry, local video assets, and GPT Realtime playback require local acceptance.")


if __name__ == "__main__":
    main()
