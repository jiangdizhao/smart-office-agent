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


def detection(
    visit_id: str,
    *,
    language: str = "en",
    greeting_kind: str = "new_anonymous",
    identity_id: str | None = None,
    display_name: str | None = None,
) -> dict:
    return {
        "language": language,
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
        "visitor_session_id": visit_id,
        "visit_id": visit_id,
        "session_stable": True,
        "session_age_seconds": 1.2,
        "session_recovery_count": 0,
        "returning_visitor": greeting_kind != "new_anonymous",
        "greeting_kind": greeting_kind,
        "identity_id": identity_id,
        "display_name": display_name,
        "identity_similarity": 0.81 if identity_id else None,
    }


def main() -> None:
    client = TestClient(app)
    anonymous_conversation = "conversation-visit-lifecycle-contract"

    initial = client.get(
        f"/api/conversations/{anonymous_conversation}",
        params={"language": "en", "actor_type": "visitor"},
    )
    initial.raise_for_status()
    assert initial.json()["state"]["conversation_phase"] == "standby"

    first_visit = detection("visit_anonymous_1")
    greeting = client.post(
        f"/api/conversations/{anonymous_conversation}/proximity-greeting",
        json=first_visit,
    )
    greeting.raise_for_status()
    greeting_payload = greeting.json()
    assert greeting_payload["triggered"] is True
    assert greeting_payload["visit_id"] == "visit_anonymous_1"
    assert greeting_payload["greeting"].startswith("Welcome to our office.")
    assert "I am Sara" in greeting_payload["greeting"]
    assert greeting_payload["conversation_phase"] == "awaiting_user"

    duplicate = client.post(
        f"/api/conversations/{anonymous_conversation}/proximity-greeting",
        json={**first_visit, "stable_frames": 5},
    )
    duplicate.raise_for_status()
    assert duplicate.json()["triggered"] is False
    assert duplicate.json()["reason"] == "visit_already_greeted"

    started = client.post(
        f"/api/conversations/{anonymous_conversation}/turn-start",
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
        f"/api/conversations/{anonymous_conversation}/turn-complete",
        json={
            "text": "Great. Which Smart Office feature would you like to try?",
            "route": "general_chat",
            "expect_reply": True,
            "source": "contract",
        },
    )
    completed.raise_for_status()
    assert completed.json()["conversation_phase"] == "awaiting_user"

    anonymous_end = client.post(
        f"/api/conversations/{anonymous_conversation}/visit-end",
        json={
            "visitor_session_id": "visit_anonymous_1",
            "language": "en",
            "reason": "contract_primary_absent",
        },
    )
    anonymous_end.raise_for_status()
    anonymous_end_payload = anonymous_end.json()
    assert anonymous_end_payload["ended"] is True
    assert anonymous_end_payload["memory_saved"] is False
    assert anonymous_end_payload["anonymous_history_discarded"] is True
    assert anonymous_end_payload["conversation_phase"] == "standby"

    anonymous_context = client.get(
        f"/api/conversations/{anonymous_conversation}",
        params={"language": "en", "actor_type": "visitor"},
    )
    anonymous_context.raise_for_status()
    anonymous_state = anonymous_context.json()["state"]
    assert anonymous_state["conversation_phase"] == "standby"
    assert anonymous_state["visit_id"] is None
    assert anonymous_state["recent_messages"] == []
    assert anonymous_state["registered_memory_summary"] == ""

    # A different Visit may greet immediately; there is no browser-wide 30-second cooldown.
    next_visit = client.post(
        f"/api/conversations/{anonymous_conversation}/proximity-greeting",
        json=detection("visit_anonymous_2"),
    )
    next_visit.raise_for_status()
    assert next_visit.json()["triggered"] is True
    assert next_visit.json()["visit_id"] == "visit_anonymous_2"
    client.post(
        f"/api/conversations/{anonymous_conversation}/visit-end",
        json={"visitor_session_id": "visit_anonymous_2", "language": "en"},
    ).raise_for_status()

    returning = client.post(
        "/api/conversations/conversation-returning-anonymous/proximity-greeting",
        json=detection(
            "visit_returning_anonymous",
            greeting_kind="returning_anonymous",
        ),
    )
    returning.raise_for_status()
    assert returning.json()["greeting"].startswith("Welcome back.")
    assert "I am Sara" in returning.json()["greeting"]

    registered_conversation = "conversation-registered-memory-contract"
    registered_identity = "person_contract_rico"
    registered_first = client.post(
        f"/api/conversations/{registered_conversation}/proximity-greeting",
        json=detection(
            "visit_rico_1",
            greeting_kind="registered_identity",
            identity_id=registered_identity,
            display_name="Rico",
        ),
    )
    registered_first.raise_for_status()
    registered_first_payload = registered_first.json()
    assert registered_first_payload["greeting"] == "Welcome back, Rico."
    assert "I am Sara" not in registered_first_payload["greeting"]
    assert "PowerPoint" not in registered_first_payload["greeting"]
    assert registered_first_payload["registered_return"] is True

    client.post(
        f"/api/conversations/{registered_conversation}/turn-start",
        json={
            "language": "en",
            "actor_type": "visitor",
            "text": "Please remember that I prefer PowerPoint demonstrations.",
            "source": "voice",
        },
    ).raise_for_status()
    client.post(
        f"/api/conversations/{registered_conversation}/turn-complete",
        json={
            "text": "Certainly. We can continue with PowerPoint next time.",
            "route": "general_chat",
            "expect_reply": True,
            "source": "contract",
        },
    ).raise_for_status()

    registered_end = client.post(
        f"/api/conversations/{registered_conversation}/visit-end",
        json={
            "visitor_session_id": "visit_rico_1",
            "identity_id": registered_identity,
            "display_name": "Rico",
            "language": "en",
            "reason": "contract_primary_absent",
        },
    )
    registered_end.raise_for_status()
    assert registered_end.json()["ended"] is True
    assert registered_end.json()["memory_saved"] is True
    assert registered_end.json()["anonymous_history_discarded"] is False

    registered_second = client.post(
        f"/api/conversations/{registered_conversation}/proximity-greeting",
        json=detection(
            "visit_rico_2",
            greeting_kind="registered_identity",
            identity_id=registered_identity,
            display_name="Rico",
        ),
    )
    registered_second.raise_for_status()
    registered_second_payload = registered_second.json()
    assert registered_second_payload["triggered"] is True
    assert registered_second_payload["visit_id"] == "visit_rico_2"
    assert registered_second_payload["greeting"] == "Welcome back, Rico."
    assert registered_second_payload["registered_memory_loaded"] is True

    registered_context = client.get(
        f"/api/conversations/{registered_conversation}",
        params={"language": "en", "actor_type": "visitor"},
    )
    registered_context.raise_for_status()
    registered_state = registered_context.json()["state"]
    assert registered_state["visit_id"] == "visit_rico_2"
    assert registered_state["identity_id"] == registered_identity
    assert "PowerPoint" in registered_state["registered_memory_summary"]
    assert all(
        "Please remember" not in message["text"]
        for message in registered_state["recent_messages"]
    )

    registered_zh = client.post(
        "/api/conversations/conversation-registered-zh/proximity-greeting",
        json=detection(
            "visit_rico_zh",
            language="zh",
            greeting_kind="registered_identity",
            identity_id=registered_identity,
            display_name="Rico",
        ),
    )
    registered_zh.raise_for_status()
    assert registered_zh.json()["greeting"] == "欢迎回来，Rico。"
    assert "我是 Sara" not in registered_zh.json()["greeting"]

    controller = read("ui/smart-office-ui/src/voice/useOfficeVoiceController.ts")
    host = read("ui/smart-office-ui/src/virtual-host/VirtualHostApp.tsx")
    avatar = read("ui/smart-office-ui/src/virtual-host/VirtualHostAvatar.tsx")
    detector = read("ui/smart-office-ui/src/vision/proximityFaceMonitor.ts")
    remote_client = read("ui/smart-office-ui/src/vision/remoteVisionClient.ts")
    proximity_hook = read("ui/smart-office-ui/src/vision/useProximityGreeting.ts")
    proactive_loop = read("ui/smart-office-ui/src/vision/proactiveReceptionVoiceLoop.ts")
    memory_store = read("backend/app/visitor_memory_store.py")
    conversation_store = read("backend/app/conversation_store.py")
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
        "suppressUntilAbsent",
        "mediapipe-person+face",
    ):
        assert needle in detector, f"Missing MediaPipe fallback contract: {needle}"

    for needle in (
        "schema_version",
        "rtx-vision-phase6",
        "RemoteVisitEnded",
        "onVisitEnded",
        "visit_ended",
        "visitor_session_expired",
        "primary.visible",
        "visit_id",
    ):
        assert needle in remote_client, f"Missing Phase 6 remote-visit contract: {needle}"

    for needle in (
        "PRIMARY_ABSENCE_GRACE_MS = 2_000",
        "proactiveLoopPromiseRef",
        "visitClosingPromiseRef",
        "queuedGreetingRef",
        "await loopPromise",
        "/visit-end",
        "VISIT_END_ATTEMPTS = 3",
        "speakFarewellReliably",
        "browserFarewellFallback",
        "感谢您的来访，欢迎下次再来。",
        "Thank you for visiting. We hope to see you again soon.",
        "next-visitor-greeting-queued",
        "next-visitor-greeting-started",
        "visit-end-synchronized",
        "captureAutomaticRealtimeTurn",
    ):
        assert needle in proximity_hook, f"Missing serialized visit-shutdown contract: {needle}"

    for needle in (
        "registered_visitor_memory",
        "identity_id TEXT PRIMARY KEY",
        "memory_summary",
        "recent_messages_json",
    ):
        assert needle in memory_store, f"Missing registered memory persistence contract: {needle}"

    for needle in (
        "visit_id",
        "registered_memory_summary",
        "last_greeted_visit_id",
        "visit_already_greeted",
        "end_visit",
        "_reset_for_new_visit_locked",
    ):
        assert needle in conversation_store, f"Missing visit/context isolation contract: {needle}"

    assert "SMART_OFFICE_PROXIMITY_GREETING_COOLDOWN_SECONDS" not in conversation_store
    assert "last_proximity_greeting_at" not in conversation_store

    for needle in (
        "END_SILENCE_MS = 900",
        "SPEECH_START_TIMEOUT_MS = 12_000",
        "controller().beginListening()",
        "controller().endListening()",
        "realtimeAgent.abortCapture()",
    ):
        assert needle in proactive_loop, f"Missing automatic Realtime voice-turn contract: {needle}"

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

    print(
        "PASS: visits reset without a global cooldown; shutdown is serialized; "
        "anonymous history is discarded; registered memory is restored only by identity_id."
    )
    print(
        "NOTE: Real LAN ordering, browser audio permission, farewell playback, camera identity "
        "thresholds, and GPT Realtime behavior still require local acceptance."
    )


if __name__ == "__main__":
    main()
