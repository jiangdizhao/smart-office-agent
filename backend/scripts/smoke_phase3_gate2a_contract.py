from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
from app.models import ToolResult, VerificationResult  # noqa: E402
from app.presentation_actions import _merge_monitor_verification  # noqa: E402
import app.turn_api as turn_api  # noqa: E402


def _post_tool_call(
    client: TestClient,
    *,
    actor: str,
    text: str,
    name: str,
    arguments: dict | None = None,
    language: str = "zh",
) -> dict:
    response = client.post(
        "/agent/turn",
        json={
            "conversation_id": f"gate2a-{actor}-{name}-{language}",
            "text": text,
            "language": language,
            "input_source": "voice",
            "actor_context": {"type": actor, "source": "contract"},
            "realtime_tool_call": {
                "name": name,
                "arguments": arguments or {},
                "call_id": "call-contract",
                "source": "gpt_realtime",
            },
        },
    )
    response.raise_for_status()
    return response.json()


def _fake_execution(name: str, arguments: dict):
    slide = int(arguments.get("slide_number", 2))
    requested = {"current_slide": slide} if name == "presentation_go_to_slide" else {}
    tool = ToolResult(
        tool_name=name,
        ok=True,
        message="Fake PowerPoint action accepted.",
        expected_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
        data={
            "execution_mode": "real",
            "requested_state": requested,
        },
    )
    verification = VerificationResult(
        ok=True,
        message="Fake observed PowerPoint state verified on DISPLAY2.",
        process_ok=True,
        window_ok=True,
        expected_process_names=["POWERPNT.EXE"],
        found_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
        found_window_titles=["Loss.pptx - PowerPoint"],
        require_window_match=True,
        checked_at=datetime.now(UTC),
        raw={"monitor_ok": True},
    )
    status = ToolResult(
        tool_name="presentation_get_status",
        ok=True,
        message="Fake status.",
        data={
            "presentation_open": True,
            "slideshow_active": True,
            "current_slide": slide,
            "total_slides": 12,
            "target_monitor_device": r"\\.\DISPLAY2",
            "slideshow_monitor_device": r"\\.\DISPLAY2",
            "monitor_placement_enforced": True,
        },
    )
    return tool, verification, status


def _fake_status_with_unverified_monitor(name: str, arguments: dict):
    assert name == "presentation_get_status"
    tool = ToolResult(
        tool_name=name,
        ok=True,
        message="Presentation status inspected.",
        expected_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
        data={"execution_mode": "real", "requested_state": {}},
    )
    verification = VerificationResult(
        ok=True,
        message="Presentation status query completed.",
        process_ok=None,
        window_ok=None,
        checked_at=datetime.now(UTC),
        raw={"verification_type": "powerpoint_state"},
    )
    status = ToolResult(
        tool_name="presentation_get_status",
        ok=True,
        message="Presentation status inspected.",
        data={
            "presentation_open": True,
            "slideshow_active": True,
            "current_slide": 4,
            "total_slides": 12,
            "target_monitor_device": r"\\.\DISPLAY2",
            "slideshow_monitor_device": None,
            "monitor_placement_enforced": False,
        },
    )
    return tool, verification, status


def main() -> None:
    client = TestClient(app)

    health = client.get("/")
    health.raise_for_status()
    health_payload = health.json()
    assert health_payload["phase"] == "m3a_fusion_phase_3_gate_2a"
    assert health_payload["capabilities"]["realtime_presentation_function_calling"] is True
    assert health_payload["capabilities"]["presentation_execution_via_turn"] is True
    assert health_payload["capabilities"]["presentation_secondary_display"] is True
    assert health_payload["capabilities"]["general_office_execution_via_turn"] is False

    visitor = _post_tool_call(
        client,
        actor="visitor",
        text="下一页",
        name="presentation_next_slide",
    )
    assert visitor["route"] == "office_direct"
    assert visitor["permission_decision"] == "denied"
    assert visitor["intent_source"] == "gpt_realtime_function_call"
    assert visitor["tool_result"] is None

    original = turn_api.execute_presentation_tool_call
    turn_api.execute_presentation_tool_call = _fake_execution
    try:
        employee = _post_tool_call(
            client,
            actor="employee",
            text="请翻到第五页",
            name="presentation_go_to_slide",
            arguments={"slide_number": 5},
        )
    finally:
        turn_api.execute_presentation_tool_call = original

    assert employee["route"] == "office_direct"
    assert employee["permission_decision"] == "allowed"
    assert employee["intent_source"] == "gpt_realtime_function_call"
    assert employee["realtime_tool_call"]["name"] == "presentation_go_to_slide"
    assert employee["tool_result"]["ok"] is True
    assert employee["verification_result"]["ok"] is True
    assert employee["presentation_status"]["current_slide"] == 5
    assert employee["presentation_status"]["slideshow_monitor_device"] == r"\\.\DISPLAY2"
    assert employee["presentation_status"]["monitor_placement_enforced"] is True
    assert "第 5 页" in employee["spoken_text"]

    # A read-only status query must answer the current slide even when the
    # independent monitor probe cannot verify DISPLAY2 at that instant.
    baseline_verification = VerificationResult(
        ok=True,
        message="Presentation status query completed.",
        checked_at=datetime.now(UTC),
        raw={},
    )
    merged = _merge_monitor_verification(
        "presentation_get_status",
        baseline_verification,
        {
            "target_monitor_device": r"\\.\DISPLAY2",
            "monitor_placement_enforced": False,
        },
        slideshow_active=True,
    )
    assert merged.ok is True

    turn_api.execute_presentation_tool_call = _fake_status_with_unverified_monitor
    try:
        status_answer = _post_tool_call(
            client,
            actor="employee",
            text="What slide are we on?",
            name="presentation_get_status",
            language="en",
        )
    finally:
        turn_api.execute_presentation_tool_call = original

    assert status_answer["verification_result"]["ok"] is True
    assert "slide 4 of 12" in status_answer["spoken_text"]
    assert not any("\u3400" <= character <= "\u9fff" for character in status_answer["spoken_text"])

    invalid = _post_tool_call(
        client,
        actor="employee",
        text="执行未知操作",
        name="presentation_unknown_action",
    )
    assert invalid["permission_decision"] == "allowed"
    assert invalid["tool_result"]["ok"] is False
    assert invalid["verification_result"]["ok"] is False

    reception = client.post(
        "/agent/turn",
        json={
            "conversation_id": "gate2a-reception",
            "text": "请介绍一下你们的解决方案",
            "language": "zh",
            "input_source": "voice",
            "actor_context": {"type": "visitor"},
            "realtime_tool_call": None,
        },
    )
    reception.raise_for_status()
    assert reception.json()["route"] == "reception_knowledge"

    print(
        "PASS: Gate 2A Realtime tool handoff, permission, current-slide status, "
        "language, verified execution response, and Phase 2 fallback are available."
    )


if __name__ == "__main__":
    main()
