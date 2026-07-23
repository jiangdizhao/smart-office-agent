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
from app.models import ApprovalRequest, ToolResult, VerificationResult  # noqa: E402
import app.office_api as office_api  # noqa: E402
import app.office_sequence as office_sequence  # noqa: E402
from app.state_store import state_store  # noqa: E402


def verified() -> VerificationResult:
    return VerificationResult(
        ok=True,
        message="Verified by Gate 3-5 contract.",
        checked_at=datetime.now(UTC),
        raw={"email_send_enabled": False},
    )


def fake_executor():
    state = {
        "presentation_open": True,
        "slideshow_active": True,
        "current_slide": 5,
        "total_slides": 9,
        "target_monitor_device": r"\\.\DISPLAY2",
        "slideshow_monitor_device": r"\\.\DISPLAY2",
        "monitor_placement_enforced": True,
        "volume_percent": 50,
        "brightness_percent": 70,
        "email_send_enabled": False,
    }

    def execute(name: str, arguments: dict):
        data: dict = {"execution_mode": "real", "requested_state": {}}
        artifacts: list[str] = []
        if name == "system_set_volume":
            state["volume_percent"] = int(arguments["value_percent"])
            data.update(
                {
                    "requested_state": {"volume_percent": state["volume_percent"]},
                    "volume_percent": state["volume_percent"],
                }
            )
        elif name == "system_adjust_brightness":
            state["brightness_percent"] = max(
                0,
                min(100, int(state["brightness_percent"]) + int(arguments["delta_percent"])),
            )
            data.update(
                {
                    "requested_state": {"brightness_percent": state["brightness_percent"]},
                    "brightness_percent": state["brightness_percent"],
                }
            )
        elif name == "office_generate_presentation_summary":
            data.update(
                {
                    "requested_state": {"summary_created": True},
                    "summary_created": True,
                    "summary_path": str(BACKEND_DIR.parent / "demo_files" / "LOG" / "presentation_summary_contract.md"),
                    "summary_path_relative": "demo_files/LOG/presentation_summary_contract.md",
                    "artifact_url": "/api/office/artifacts/presentation_summary_contract.md",
                }
            )
            artifacts.append(data["summary_path"])
        elif name == "gmail_create_summary_draft":
            data.update(
                {
                    "requested_state": {"gmail_draft_created": True},
                    "gmail_draft_created": True,
                    "gmail_draft_id": "draft-contract-123",
                    "gmail_drafts_url": "https://mail.google.com/mail/u/0/#drafts",
                    "recipient_email": "jiangdizhao@gmail.com",
                    "email_send_enabled": False,
                    "sent": False,
                }
            )
        elif name == "presentation_end_slideshow":
            state["slideshow_active"] = False
        else:
            raise AssertionError(f"Unexpected fake office tool: {name}")

        status_data = {**state, **data}
        result = ToolResult(
            tool_name=name,
            ok=True,
            message=f"Fake executed {name}.",
            artifacts=artifacts,
            data=data,
            raw={"email_send_enabled": False},
        )
        status = ToolResult(
            tool_name="office_get_status",
            ok=True,
            message="Fake office status.",
            data=status_data,
        )
        return result, verified(), status

    return execute


def post_office_plan(
    client: TestClient,
    *,
    actor: str,
    text: str,
    steps: list[dict],
    language: str = "zh",
):
    response = client.post(
        "/agent/office-turn",
        json={
            "conversation_id": f"gate3-5-{actor}-{time.time_ns()}",
            "text": text,
            "language": language,
            "input_source": "voice",
            "actor_context": {"type": actor},
            "realtime_tool_call": {
                "name": "office_plan",
                "arguments": {"steps": steps},
                "source": "gpt_realtime",
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def run_task_with_approval(task_id: str) -> None:
    runner = asyncio.create_task(office_sequence.run_office_task(task_id))
    waiting_step = None
    for _ in range(80):
        task = state_store.get_task(task_id)
        if task is not None:
            waiting_step = next(
                (step for step in task.steps if step.status == "waiting_approval"),
                None,
            )
        if waiting_step is not None:
            break
        await asyncio.sleep(0.025)
    assert waiting_step is not None, "Gmail draft step did not reach the approval gate."
    state_store.set_approval(
        task_id,
        waiting_step.step_id,
        ApprovalRequest(action="approve", note="Gate 3-5 contract approval"),
    )
    await asyncio.wait_for(runner, timeout=5.0)


def main() -> None:
    client = TestClient(app)

    root = client.get("/").json()
    assert root["phase"] == "m3a_fusion_phase_3_gate_3_5"
    assert root["capabilities"]["system_volume_control"] is True
    assert root["capabilities"]["system_brightness_control"] is True
    assert root["capabilities"]["presentation_summary_artifacts"] is True
    assert root["capabilities"]["gmail_draft_creation"] is True
    assert root["capabilities"]["email_send_enabled"] is False

    volume, error = office_sequence.validate_office_plan(
        {"steps": [{"name": "system_set_volume", "value_percent": 35}]}
    )
    assert error is None and volume is not None
    assert volume[0].args == {"value_percent": 35}
    assert volume[0].requires_confirmation is False

    workflow, error = office_sequence.validate_office_plan(
        {
            "steps": [
                {"name": "system_adjust_brightness", "delta_percent": -10},
                {"name": "office_generate_presentation_summary", "language": "zh"},
                {
                    "name": "gmail_create_summary_draft",
                    "language": "zh",
                    "summary_source": "latest",
                },
            ]
        }
    )
    assert error is None and workflow is not None and len(workflow) == 3
    assert workflow[-1].requires_confirmation is True

    invalid, error = office_sequence.validate_office_plan(
        {
            "steps": [
                {
                    "name": "gmail_create_summary_draft",
                    "language": "zh",
                    "summary_source": "latest",
                    "recipient_email": "attacker@example.com",
                }
            ]
        }
    )
    assert invalid is None and error is not None

    visitor = post_office_plan(
        client,
        actor="visitor",
        text="把音量调到35%。",
        steps=[{"name": "system_set_volume", "value_percent": 35}],
    )
    assert visitor["permission_decision"] == "denied"
    assert visitor["task_id"] is None

    fake = fake_executor()
    original_direct = office_api.execute_office_tool_call
    office_api.execute_office_tool_call = fake
    try:
        direct = post_office_plan(
            client,
            actor="employee",
            text="把音量调到35%。",
            steps=[{"name": "system_set_volume", "value_percent": 35}],
        )
    finally:
        office_api.execute_office_tool_call = original_direct
    assert direct["route"] == "office_direct"
    assert direct["tool_result"]["tool_name"] == "system_set_volume"
    assert direct["verification_result"]["ok"] is True
    assert direct["office_status"]["volume_percent"] == 35

    async def no_background_runner(_task_id: str) -> None:
        return None

    original_background = office_api.run_office_task
    office_api.run_office_task = no_background_runner
    try:
        scheduled = post_office_plan(
            client,
            actor="employee",
            text="结束演示，生成摘要，并准备Gmail草稿。",
            steps=[
                {"name": "presentation_end_slideshow"},
                {"name": "office_generate_presentation_summary", "language": "zh"},
                {
                    "name": "gmail_create_summary_draft",
                    "language": "zh",
                    "summary_source": "latest",
                },
            ],
        )
    finally:
        office_api.run_office_task = original_background

    assert scheduled["route"] == "office_planned_task"
    assert scheduled["approval_required"] is True
    task_id = scheduled["task_id"]
    assert task_id

    original_sequence_executor = office_sequence.execute_office_tool_call
    office_sequence.execute_office_tool_call = fake
    try:
        asyncio.run(run_task_with_approval(task_id))
    finally:
        office_sequence.execute_office_tool_call = original_sequence_executor

    task = client.get(f"/agent/tasks/{task_id}").json()
    assert task["status"] == "completed"
    assert [step["status"] for step in task["steps"]] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    gmail = task["steps"][-1]["result"]["data"]
    assert gmail["gmail_draft_created"] is True
    assert gmail["sent"] is False
    assert gmail["email_send_enabled"] is False

    interpreter = (
        BACKEND_DIR.parent
        / "ui"
        / "smart-office-ui"
        / "src"
        / "voice"
        / "realtimeOfficeInterpreter.ts"
    ).read_text(encoding="utf-8")
    assert "name: 'office_plan'" in interpreter
    assert "gmail_create_summary_draft" in interpreter
    assert "Email sending is disabled" in interpreter
    assert "gmail_send" not in interpreter

    artifacts_source = (BACKEND_DIR / "app" / "office_artifacts.py").read_text(encoding="utf-8")
    assert ".drafts()" in artifacts_source
    assert ".create(userId=\"me\"" in artifacts_source
    assert ".send(" not in artifacts_source

    print(
        "PASS: Phase 3 Gate 3-5 validates bounded volume/brightness actions, local "
        "summary artifacts, approval-gated Gmail draft creation, visitor denial, and "
        "the invariant that email sending is disabled."
    )


if __name__ == "__main__":
    main()
