from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402


def main() -> None:
    client = TestClient(app)

    health = client.get("/")
    health.raise_for_status()
    health_payload = health.json()
    assert health_payload["capabilities"]["task_runtime"] is True
    assert health_payload["capabilities"]["realtime_voice_api"] is True
    assert health_payload["capabilities"]["agent_turn_api"] is True

    realtime = client.get("/api/realtime/status")
    realtime.raise_for_status()
    realtime_payload = realtime.json()
    assert realtime_payload["transport"] == "webrtc"
    assert realtime_payload["turn_mode"] == "push_to_talk"
    assert realtime_payload["initial_output_modalities"] == ["text"]

    turn_status = client.get("/agent/turn/status")
    turn_status.raise_for_status()
    assert turn_status.json()["task_creation_enabled"] is False

    turn = client.post(
        "/agent/turn",
        json={
            "conversation_id": "phase1-smoke",
            "text": "你好",
            "language": "zh",
            "input_source": "text",
            "actor_context": {"type": "employee"},
        },
    )
    turn.raise_for_status()
    turn_payload = turn.json()
    assert turn_payload["route"] == "realtime_direct"
    assert turn_payload["task_id"] is None
    assert "Smart Office" in turn_payload["spoken_text"]

    unclear = client.post(
        "/agent/turn",
        json={
            "conversation_id": "phase1-smoke",
            "text": "__UNCLEAR__",
            "language": "en",
            "input_source": "voice",
        },
    )
    unclear.raise_for_status()
    assert unclear.json()["route"] == "clarification"

    # The Realtime proxy must reject session creation before reading an SDP offer
    # when no server-side API key is configured. This also proves the key is not
    # expected from the browser.
    previous_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        missing_key = client.post(
            "/api/realtime/session?conversation_id=phase1-smoke",
            content="v=0\r\n",
            headers={"Content-Type": "application/sdp"},
        )
        assert missing_key.status_code == 503
        assert "OPENAI_API_KEY" in missing_key.text
    finally:
        if previous_key is not None:
            os.environ["OPENAI_API_KEY"] = previous_key

    # Existing Milestone 2 task creation remains available.
    task = client.post(
        "/agent/tasks",
        json={"text": "meeting prepare", "execute": False},
    )
    task.raise_for_status()
    task_payload = task.json()
    assert task_payload["task_id"]
    assert isinstance(task_payload["steps"], list)

    print("PASS: Phase 1 voice API and Milestone 2 task contracts are available.")


if __name__ == "__main__":
    main()
