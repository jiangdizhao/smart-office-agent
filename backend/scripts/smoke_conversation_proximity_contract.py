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
    assert greeting_payload["greeting"].startswith("Welcome to our office.")
    assert "I am Sara" in greeting_payload["greeting"]
    assert "Would you like to try a quick demonstration?" in greeting_payload["greeting"]
    assert greeting_payload["conversation_phase"] == "awaiting_user"
    assert greeting_payload["proactive_reception"] is True

    duplicate = client.post(
        f"/api/conversations/{conversation_id}/proximity-greeting",
        json={**detection, "stable_frames": 5},
    )
    duplicate.raise_for_status()
    duplicate_payload = duplicate.json()
    assert duplicate_payload["triggered"] is False
    assert duplicate_payload["reason"] == "conversation_phase=awaiting_user"
    assert duplicate_payload["conversation_phase"] == "awaiting_user"

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
    assert returning.json()["greeting"].startswith("Welcome back.")
    assert returning.json()["conversation_phase"] == "awaiting_user"

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
    assert registered.json()["greeting"].startswith("Welcome back, Rico.")

    started = client.post(
        f"/api/conversations/{conversation_id}/turn-start",
        json={
            "language": "en",
            "actor_type": "visitor",
            "text": "Yes, please.",
            "source": "voice",
        },
    )
    started.raise_for_status()
    assert started.json()["conversation_phase"] == "engaged"

    completed = client.post(
        f"/api/conversations/{conversation_id}/turn-complete",
        json={
            "text": "Great. Would you like to try PowerPoint voice control, Outlook assistance, or ask a general question?",
            "route": "general_chat",
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
    assert "Yes, please." in state["conversation_summary"]

    standby = client.post(f"/api/conversations/{conversation_id}/standby")
    standby.raise_for_status()
    assert standby.json()["conversation_phase"] == "standby"

    controller = read("ui/smart-office-ui/src/voice/useOfficeVoiceController.ts")
    host = read("ui/smart-office-ui/src/virtual-host/VirtualHostApp.tsx")
    avatar = read("ui/smart-office-ui/src/virtual-host/VirtualHostAvatar.tsx")
    detector = read("ui/smart-office-ui/src/vision/proximityFaceMonitor.ts")
    remote_client = read("ui/smart-office-ui/src/vision/remoteVisionClient.ts")
    proximity_hook = read("ui/smart-office-ui/src/vision/useProximityGreeting.ts")
    listening_gate = read("ui/smart-office-ui/src/vision/proactiveListeningGate.ts")
    proactive_loop = read("ui/smart-office-ui/src/vision/proactiveReceptionVoiceLoop.ts")
    stage1_css = read("ui/smart-office-ui/src/virtual-host/ProactiveReceptionStage1.css")
    main_tsx = read("ui/smart-office-ui/src/main.tsx")
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
        "ProactiveListeningGate",
        "enterBodyRatio",
        "exitBodyRatio",
        "enterStableMs",
        "exitGraceMs",
        "face_missing",
        "visitor_too_far",
        "primary_absent",
        "face_missing_grace",
        "visitor_too_far_grace",
    ):
        assert needle in listening_gate, f"Missing listening-gate hysteresis contract: {needle}"

    for needle in (
        "smartoffice:host-intro-start",
        "Welcome to our office.",
        "Welcome back",
        "VITE_VISION_SOURCE",
        "greetedVisitRef",
        "captureAutomaticRealtimeTurn",
        "proactive-reception-started",
        "proactive-reception-listening-suspended",
        "proactive-listening-gate",
        "VITE_PROACTIVE_LISTEN_ENTER_BODY_RATIO",
        "VITE_PROACTIVE_LISTEN_EXIT_BODY_RATIO",
        "VITE_PROACTIVE_LISTEN_MIN_FACE_CONFIDENCE",
        "VITE_PROACTIVE_LISTEN_ENTER_STABLE_MS",
        "VITE_PROACTIVE_LISTEN_EXIT_GRACE_MS",
        "VITE_PROACTIVE_SESSION_ABSENCE_SECONDS",
        "scheduleVisitorAbsence",
    ):
        assert needle in proximity_hook, f"Missing presence/listening separation contract: {needle}"

    for needle in (
        "END_SILENCE_MS = 900",
        "SPEECH_START_TIMEOUT_MS = 12_000",
        "controller().beginListening()",
        "controller().endListening()",
        "realtimeAgent.abortCapture()",
        "listeningGate",
        "kind: 'gated'",
    ):
        assert needle in proactive_loop, f"Missing gated automatic Realtime voice-turn contract: {needle}"

    assert ".voice-primary-row" in stage1_css
    assert ".conversation-record-button" in stage1_css
    assert ".primary-voice-button" in stage1_css
    assert "display: none !important" in stage1_css
    assert "./virtual-host/ProactiveReceptionStage1.css" in main_tsx

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

    print("PASS: proactive reception keeps the visitor session while face/distance hysteresis gates the real GPT Realtime microphone.")
    print("NOTE: Real LAN, camera geometry, room-noise VAD thresholds, local video assets, and GPT Realtime playback require local acceptance.")


if __name__ == "__main__":
    main()
