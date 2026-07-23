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
import app.turn_api as turn_api  # noqa: E402


def verified() -> VerificationResult:
    return VerificationResult(
        ok=True,
        message="Fake presentation state verified.",
        process_ok=True,
        window_ok=True,
        checked_at=datetime.now(UTC),
        raw={"monitor_ok": True},
    )


def fake_executor():
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
            state.update(
                presentation_open=True,
                slideshow_active=True,
                current_slide=1,
                slideshow_monitor_device=r"\\.\DISPLAY2",
                monitor_placement_enforced=True,
            )
        elif name == "presentation_go_to_slide":
            state["current_slide"] = int(arguments["slide_number"])
        elif name == "presentation_next_slide":
            state["current_slide"] = int(state["current_slide"] or 0) + 1
        elif name == "presentation_previous_slide":
            state["current_slide"] = max(1, int(state["current_slide"] or 1) - 1)
        elif name == "presentation_end_slideshow":
            state.update(
                slideshow_active=False,
                current_slide=None,
                slideshow_monitor_device=None,
                monitor_placement_enforced=False,
            )
        tool = ToolResult(
            tool_name=name,
            ok=True,
            message=f"Fake executed {name}.",
            data={"execution_mode": "real", "requested_state": {}},
        )
        status = ToolResult(
            tool_name="presentation_get_status",
            ok=True,
            message="Fake status.",
            data=dict(state),
        )
        return tool, verified(), status

    return execute


def post_plan(client: TestClient, *, actor: str, text: str, steps: list[dict], language: str) -> dict:
    response = client.post(
        "/agent/turn",
        json={
            "conversation_id": f"unified-plan-{actor}-{language}-{len(steps)}",
            "text": text,
            "language": language,
            "input_source": "voice",
            "actor_context": {"type": actor},
            "realtime_tool_call": {
                "name": "presentation_plan",
                "arguments": {"steps": steps},
                "call_id": "unified-plan-contract",
                "source": "gpt_realtime",
            },
        },
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    client = TestClient(app)
    turn_status = client.get("/agent/turn/status").json()
    assert turn_status["unified_presentation_plan_enabled"] is True

    one, error = presentation_sequence.validate_sequence_arguments(
        {"steps": [{"name": "presentation_get_status"}]}
    )
    assert error is None and one is not None and len(one) == 1

    many, error = presentation_sequence.validate_sequence_arguments(
        {
            "steps": [
                {"name": "presentation_open_configured"},
                {"name": "presentation_start_slideshow"},
                {"name": "presentation_go_to_slide", "slide_number": 5},
            ]
        }
    )
    assert error is None and many is not None and len(many) == 3

    visitor = post_plan(
        client,
        actor="visitor",
        text="下一页",
        steps=[{"name": "presentation_next_slide"}],
        language="zh",
    )
    assert visitor["permission_decision"] == "denied"
    assert visitor["task_id"] is None

    fake = fake_executor()
    original_direct = turn_api.execute_presentation_tool_call
    turn_api.execute_presentation_tool_call = fake
    try:
        single = post_plan(
            client,
            actor="employee",
            text="What slide are we on?",
            steps=[{"name": "presentation_get_status"}],
            language="en",
        )
    finally:
        turn_api.execute_presentation_tool_call = original_direct

    assert single["route"] == "office_direct"
    assert single["realtime_tool_call"]["name"] == "presentation_plan"
    assert single["tool_result"]["tool_name"] == "presentation_get_status"
    assert single["verification_result"]["ok"] is True
    assert not any("\u3400" <= char <= "\u9fff" for char in single["spoken_text"])

    async def no_background_runner(_task_id: str) -> None:
        return None

    original_turn_runner = turn_api.run_presentation_sequence_task
    turn_api.run_presentation_sequence_task = no_background_runner
    try:
        compound = post_plan(
            client,
            actor="employee",
            text="Open the presentation, start the slide show, then go to slide five.",
            steps=[
                {"name": "presentation_open_configured"},
                {"name": "presentation_start_slideshow"},
                {"name": "presentation_go_to_slide", "slide_number": 5},
            ],
            language="en",
        )
    finally:
        turn_api.run_presentation_sequence_task = original_turn_runner

    assert compound["route"] == "office_planned_task"
    assert compound["tool_result"]["tool_name"] == "presentation_plan"
    task_id = compound["task_id"]
    assert task_id

    original_sequence_executor = presentation_sequence.execute_presentation_tool_call
    presentation_sequence.execute_presentation_tool_call = fake
    try:
        asyncio.run(presentation_sequence.run_presentation_sequence_task(task_id))
    finally:
        presentation_sequence.execute_presentation_tool_call = original_sequence_executor

    task = client.get(f"/agent/tasks/{task_id}").json()
    assert task["status"] == "completed"
    assert [step["status"] for step in task["steps"]] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    final_status = task["steps"][-1]["result"]["data"]["presentation_status"]
    assert final_status["current_slide"] == 5
    assert final_status["monitor_placement_enforced"] is True

    invalid = post_plan(
        client,
        actor="employee",
        text="Do something to the presentation.",
        steps=[],
        language="en",
    )
    assert invalid["tool_result"]["ok"] is False
    assert invalid["verification_result"]["ok"] is False

    print(
        "PASS: GPT Realtime exposes one presentation_plan function; Backend dispatches "
        "one step directly and multiple steps through the verified Task Runtime."
    )


if __name__ == "__main__":
    main()
