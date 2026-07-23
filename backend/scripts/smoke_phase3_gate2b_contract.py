from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
from app.models import ToolResult, VerificationResult  # noqa: E402
import app.presentation_sequence as presentation_sequence  # noqa: E402
from app.presentation_sequence import validate_sequence_arguments  # noqa: E402


def _verification(message: str = "Fake presentation state verified.") -> VerificationResult:
    return VerificationResult(
        ok=True,
        message=message,
        process_ok=True,
        window_ok=True,
        expected_process_names=["POWERPNT.EXE"],
        found_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
        found_window_titles=["Loss.pptx - PowerPoint Slide Show"],
        require_window_match=True,
        checked_at=datetime.now(UTC),
        raw={"monitor_ok": True},
    )


def _fake_sequence_executor():
    state = {
        "presentation_open": False,
        "slideshow_active": False,
        "current_slide": None,
        "total_slides": 12,
        "target_monitor_device": r"\\.\DISPLAY2",
        "slideshow_monitor_device": None,
        "monitor_placement_enforced": False,
    }

    def execute(name: str, arguments: dict):
        if name == "presentation_open_configured":
            state["presentation_open"] = True
        elif name == "presentation_start_slideshow":
            state["presentation_open"] = True
            state["slideshow_active"] = True
            state["current_slide"] = 1
            state["slideshow_monitor_device"] = r"\\.\DISPLAY2"
            state["monitor_placement_enforced"] = True
        elif name == "presentation_go_to_slide":
            state["current_slide"] = int(arguments["slide_number"])
        elif name == "presentation_next_slide":
            state["current_slide"] = int(state["current_slide"] or 0) + 1
        elif name == "presentation_previous_slide":
            state["current_slide"] = max(1, int(state["current_slide"] or 1) - 1)
        elif name == "presentation_end_slideshow":
            state["slideshow_active"] = False
            state["current_slide"] = None
            state["slideshow_monitor_device"] = None
            state["monitor_placement_enforced"] = False

        tool = ToolResult(
            tool_name=name,
            ok=True,
            message=f"Fake executed {name}.",
            expected_process_names=["POWERPNT.EXE"],
            expected_window_keywords=["PowerPoint"],
            data={"execution_mode": "real", "requested_state": {}},
        )
        status = ToolResult(
            tool_name="presentation_get_status",
            ok=True,
            message="Fake status.",
            data=dict(state),
        )
        return tool, _verification(), status

    return execute


def _wait_for_task(client: TestClient, task_id: str) -> dict:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        response = client.get(f"/agent/tasks/{task_id}")
        response.raise_for_status()
        task = response.json()
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        time.sleep(0.05)
    raise AssertionError(f"Task did not reach a terminal state: {task_id}")


def main() -> None:
    client = TestClient(app)

    health = client.get("/")
    health.raise_for_status()
    health_payload = health.json()
    assert health_payload["phase"] == "m3a_fusion_phase_3_gate_2b"
    assert health_payload["capabilities"]["compound_presentation_execution"] is True
    assert health_payload["capabilities"]["compound_task_cancellation"] is True
    assert health_payload["capabilities"]["general_office_execution_via_turn"] is False

    single_steps, single_error = validate_sequence_arguments(
        {"steps": [{"name": "presentation_get_status"}]}
    )
    assert single_error is None
    assert single_steps is not None and len(single_steps) == 1

    valid_steps, error = validate_sequence_arguments(
        {
            "steps": [
                {"name": "presentation_open_configured"},
                {"name": "presentation_start_slideshow"},
                {"name": "presentation_go_to_slide", "slide_number": 5},
            ]
        }
    )
    assert error is None
    assert valid_steps is not None and len(valid_steps) == 3
    assert valid_steps[2].args == {"slide_number": 5}

    invalid_steps, invalid_error = validate_sequence_arguments(
        {
            "steps": [
                {"name": "presentation_open_configured"},
                {"name": "presentation_go_to_slide"},
            ]
        }
    )
    assert invalid_steps is None
    assert invalid_error is not None and invalid_error.ok is False

    visitor = client.post(
        "/agent/turn",
        json={
            "conversation_id": "gate2b-visitor",
            "text": "打开演示文稿，然后开始播放",
            "language": "zh",
            "input_source": "voice",
            "actor_context": {"type": "visitor"},
            "realtime_tool_call": {
                "name": "presentation_plan",
                "arguments": {
                    "steps": [
                        {"name": "presentation_open_configured"},
                        {"name": "presentation_start_slideshow"},
                    ]
                },
                "call_id": "visitor-plan",
                "source": "gpt_realtime",
            },
        },
    )
    visitor.raise_for_status()
    visitor_payload = visitor.json()
    assert visitor_payload["permission_decision"] == "denied"
    assert visitor_payload["task_id"] is None

    original = presentation_sequence.execute_presentation_tool_call
    presentation_sequence.execute_presentation_tool_call = _fake_sequence_executor()
    try:
        response = client.post(
            "/agent/turn",
            json={
                "conversation_id": "gate2b-employee",
                "text": "Open the presentation, start the slide show, then go to slide five.",
                "language": "en",
                "input_source": "voice",
                "actor_context": {"type": "employee"},
                "realtime_tool_call": {
                    "name": "presentation_plan",
                    "arguments": {
                        "steps": [
                            {"name": "presentation_open_configured"},
                            {"name": "presentation_start_slideshow"},
                            {"name": "presentation_go_to_slide", "slide_number": 5},
                        ]
                    },
                    "call_id": "employee-plan",
                    "source": "gpt_realtime",
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        assert payload["route"] == "office_planned_task"
        assert payload["permission_decision"] == "allowed"
        assert payload["response_language"] == "en"
        assert payload["intent_source"] == "gpt_realtime_presentation_plan"
        assert payload["task_id"]
        assert payload["tool_result"]["tool_name"] == "presentation_plan"
        assert payload["tool_result"]["data"]["step_count"] == 3
        assert not any("\u3400" <= char <= "\u9fff" for char in payload["spoken_text"])

        task = _wait_for_task(client, payload["task_id"])
        assert task["status"] == "completed"
        assert [step["status"] for step in task["steps"]] == [
            "succeeded",
            "succeeded",
            "succeeded",
        ]
        final_status = task["steps"][-1]["result"]["data"]["presentation_status"]
        assert final_status["current_slide"] == 5
        assert final_status["monitor_placement_enforced"] is True
        assert task["steps"][-1]["result"]["data"]["verification"]["ok"] is True
    finally:
        presentation_sequence.execute_presentation_tool_call = original

    async def cancellation_contract() -> None:
        slow_started = asyncio.Event()

        def slow_execute(name: str, arguments: dict):
            slow_started_loop.call_soon_threadsafe(slow_started.set)
            time.sleep(0.25)
            tool = ToolResult(
                tool_name=name,
                ok=True,
                message="Slow fake action completed.",
                data={"execution_mode": "real", "requested_state": {}},
            )
            status = ToolResult(
                tool_name="presentation_get_status",
                ok=True,
                message="Fake status.",
                data={"presentation_open": True, "slideshow_active": False},
            )
            return tool, _verification(), status

        planned, validation_error = validate_sequence_arguments(
            {
                "steps": [
                    {"name": "presentation_open_configured"},
                    {"name": "presentation_get_status"},
                ]
            }
        )
        assert validation_error is None and planned is not None
        task = presentation_sequence.create_presentation_sequence_task(
            "Open and inspect the presentation.", planned
        )
        presentation_sequence.execute_presentation_tool_call = slow_execute
        runner = asyncio.create_task(
            presentation_sequence.run_presentation_sequence_task(task.task_id)
        )
        await slow_started.wait()
        state = presentation_sequence.state_store.get_task(task.task_id)
        assert state is not None
        presentation_sequence.state_store.update_pending_steps(
            task.task_id, "cancelled", message="Contract cancellation."
        )
        presentation_sequence.state_store.set_status(
            task.task_id, "cancelled", summary="Contract cancellation."
        )
        await runner
        cancelled = presentation_sequence.state_store.get_task(task.task_id)
        assert cancelled is not None and cancelled.status == "cancelled"
        assert cancelled.steps[1].status == "cancelled"

    slow_started_loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(slow_started_loop)
        slow_started_loop.run_until_complete(cancellation_contract())
    finally:
        presentation_sequence.execute_presentation_tool_call = original
        slow_started_loop.close()
        asyncio.set_event_loop(None)

    print(
        "PASS: unified GPT Realtime presentation plans support validated multi-step execution, "
        "permission, per-step verification, final state, language, and cancellation."
    )


if __name__ == "__main__":
    main()
