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

    greeting = client.post(
        f"/api/conversations/{conversation_id}/proximity-greeting",
        json={
            "language": "en",
            "actor_type": "visitor",
            "face_area_ratio": 0.31,
            "confidence": 0.94,
            "frontal_score": 0.88,
            "center_x": 0.5,
            "center_y": 0.44,
            "stable_frames": 4,
            "detector": "contract",
        },
    )
    greeting.raise_for_status()
    greeting_payload = greeting.json()
    assert greeting_payload["triggered"] is True
    assert greeting_payload["greeting"] == "Hi there."
    assert greeting_payload["conversation_phase"] == "awaiting_user"

    duplicate = client.post(
        f"/api/conversations/{conversation_id}/proximity-greeting",
        json={
            "language": "en",
            "actor_type": "visitor",
            "face_area_ratio": 0.32,
            "confidence": 0.95,
            "frontal_score": 0.9,
            "center_x": 0.5,
            "center_y": 0.44,
            "stable_frames": 5,
            "detector": "contract",
        },
    )
    duplicate.raise_for_status()
    assert duplicate.json()["triggered"] is False
    assert duplicate.json()["reason"].startswith("conversation_phase=")

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
        "user",
        "assistant",
    ]
    assert "What can you help me with?" in state["conversation_summary"]

    controller = read("ui/smart-office-ui/src/voice/useOfficeVoiceController.ts")
    host = read("ui/smart-office-ui/src/virtual-host/VirtualHostApp.tsx")
    detector = read("ui/smart-office-ui/src/vision/proximityFaceMonitor.ts")
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
        "face_area_ratio",
        "frontal_score",
        "REQUIRED_STABLE_FRAMES",
        "suppressUntilAbsent",
        "mediapipe-face-detector",
    ):
        assert needle in detector, f"Missing proximity detector contract: {needle}"

    assert "空闲近距主动问候" in drawer
    assert "仅在待机且正脸近距离稳定出现时触发" in drawer

    print("PASS: conversation memory, lifecycle state, and idle close-face greeting contracts are present.")
    print("NOTE: Real camera geometry and GPT Realtime speech output require local browser acceptance.")


if __name__ == "__main__":
    main()
