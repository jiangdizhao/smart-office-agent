from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

import app.enhanced_turn_api as enhanced_turn_api
import app.general_chat_api as general_chat_api
from app.human_recording_api import _write_summary_docx
from app.main import app


async def _fake_general_answer(**_: object) -> tuple[str, str]:
    return "Jupiter is the largest planet in the Solar System.", "contract-general-model"


def main() -> None:
    general_chat_api.generate_general_chat_answer = _fake_general_answer
    enhanced_turn_api.generate_general_chat_answer = _fake_general_answer
    client = TestClient(app)

    health = client.get("/")
    assert health.status_code == 200, health.text
    capabilities = health.json()["capabilities"]
    assert capabilities["general_backend_chat"] is True
    assert capabilities["human_conversation_docx_summary"] is True

    general = client.post(
        "/api/general-chat",
        json={
            "conversation_id": "contract-general-chat",
            "text": "How large is Jupiter?",
            "language": "en",
            "actor_type": "visitor",
        },
    )
    assert general.status_code == 200, general.text
    assert general.json()["route"] == "general_chat"
    assert "Jupiter" in general.json()["spoken_text"]

    enhanced = client.post(
        "/agent/turn",
        json={
            "conversation_id": "contract-enhanced-turn",
            "text": "Why is the sky blue?",
            "language": "en",
            "input_source": "text",
            "actor_context": {"type": "visitor"},
            "active_task_id": None,
            "realtime_tool_call": None,
        },
    )
    assert enhanced.status_code == 200, enhanced.text
    assert enhanced.json()["intent_source"] == "backend_general_chat_llm"
    assert enhanced.json()["spoken_text"]

    missing = client.post(
        "/api/human-recordings/contract-no-recording/summary",
        json={"language": "zh"},
    )
    assert missing.status_code == 200, missing.text
    assert missing.json()["ok"] is False
    assert missing.json()["recording_available"] is False

    with tempfile.TemporaryDirectory() as temporary_directory:
        output = Path(temporary_directory) / "human_summary.docx"
        _write_summary_docx(
            document_path=output,
            summary={
                "title": "Meeting summary",
                "overview": "Two people reviewed the demonstration plan.",
                "key_points": ["The demo remains local."],
                "decisions": ["Test the recording workflow."],
                "action_items": [
                    {"owner": "Speaker A", "task": "Run the test", "deadline": "Tomorrow"}
                ],
                "open_questions": ["How well will diarization work in noise?"],
            },
            transcript="[00:00] Speaker A: Let us test the demonstration.",
            audio_path=Path("human-conversation.webm"),
            language="en",
            transcription_model="contract-transcription-model",
            summary_model="contract-summary-model",
        )
        assert output.is_file()
        assert output.stat().st_size > 0

    print("Human recording and backend general-chat contract passed.")


if __name__ == "__main__":
    asyncio.run(asyncio.to_thread(main))
