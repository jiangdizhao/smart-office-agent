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
from app.presentation_config import presentation_config  # noqa: E402
from app.state_store import state_store  # noqa: E402


def verified() -> VerificationResult:
    return VerificationResult(
        ok=True,
        message="Verified by Gate 3-5 contract.",
        checked_at=datetime.now(UTC),
        raw={
            "email_send_enabled": False,
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
        },
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
        "sender_account_email": "jiangdizhao1@outlook.com",
        "recipient_email": "jiangdizhao@gmail.com",
        "email_send_enabled": False,
        "approval_gated_email_send_enabled": True,
        "unrestricted_email_send_enabled": False,
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
        elif name == "outlook_create_summary_draft":
            data.update(
                {
                    "requested_state": {"outlook_draft_created": True},
                    "outlook_draft_created": True,
                    "outlook_draft_verified": True,
                    "outlook_draft_entry_id": "outlook-entry-contract-123",
                    "outlook_draft_store_id": "outlook-store-contract-123",
                    "outlook_draft_displayed": True,
                    "sender_account_email": state["sender_account_email"],
                    "recipient_email": state["recipient_email"],
                    "subject": "Contract draft",
                    "email_send_enabled": False,
                    "approval_gated_email_send_enabled": True,
                    "unrestricted_email_send_enabled": False,
                    "sent": False,
                }
            )
        elif name == "outlook_send_approved_draft":
            data.update(
                {
                    "requested_state": {"outlook_email_sent": True},
                    "source_outlook_draft_entry_id": "outlook-entry-contract-123",
                    "sender_account_email": state["sender_account_email"],
                    "recipient_email": state["recipient_email"],
                    "draft_notice_removed": True,
                    "send_invoked": True,
                    "approval_gated_email_send_enabled": True,
                    "unrestricted_email_send_enabled": False,
                    "sent": True,
                    "delivery_confirmed": False,
                }
            )
            state.update(data)
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
            raw={
                "email_send_enabled": False,
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
            },
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


async def run_task_with_approvals(task_id: str, expected_approvals: int) -> None:
    runner = asyncio.create_task(office_sequence.run_office_task(task_id))
    approved_steps: set[str] = set()
    for _ in range(240):
        task = state_store.get_task(task_id)
        if task is not None:
            waiting_step = next(
                (
                    step
                    for step in task.steps
                    if step.status == "waiting_approval" and step.step_id not in approved_steps
                ),
                None,
            )
            if waiting_step is not None:
                approved_steps.add(waiting_step.step_id)
                state_store.set_approval(
                    task_id,
                    waiting_step.step_id,
                    ApprovalRequest(
                        action="approve",
                        note=f"Gate 3-5 contract approval {len(approved_steps)}",
                    ),
                )
            if task.status in {"completed", "failed", "cancelled"}:
                break
        await asyncio.sleep(0.025)
    await asyncio.wait_for(runner, timeout=5.0)
    assert len(approved_steps) == expected_approvals


def main() -> None:
    client = TestClient(app)

    root = client.get("/").json()
    assert root["phase"] == "m3a_fusion_phase_3_gate_3_5"
    assert root["capabilities"]["system_volume_control"] is True
    assert root["capabilities"]["system_brightness_control"] is True
    assert root["capabilities"]["presentation_summary_artifacts"] is True
    assert root["capabilities"]["classic_outlook_draft_creation"] is True
    assert root["capabilities"]["outlook_draft_approval_gate"] is True
    assert root["capabilities"]["email_send_enabled"] is False

    assert presentation_config.outlook_sender_email == "jiangdizhao1@outlook.com"
    assert presentation_config.recipient_email == "jiangdizhao@gmail.com"
    assert (
        presentation_config.outlook_sender_email.casefold()
        != presentation_config.recipient_email.casefold()
    )

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
                    "name": "outlook_create_summary_draft",
                    "language": "zh",
                    "summary_source": "latest",
                },
                {
                    "name": "outlook_send_approved_draft",
                    "draft_source": "latest_verified",
                },
            ]
        }
    )
    assert error is None and workflow is not None and len(workflow) == 4
    assert workflow[-2].requires_confirmation is True
    assert workflow[-1].requires_confirmation is True

    invalid, error = office_sequence.validate_office_plan(
        {
            "steps": [
                {
                    "name": "outlook_create_summary_draft",
                    "language": "zh",
                    "summary_source": "latest",
                    "recipient_email": "attacker@example.com",
                }
            ]
        }
    )
    assert invalid is None and error is not None

    invalid_send, error = office_sequence.validate_office_plan(
        {
            "steps": [
                {
                    "name": "outlook_send_approved_draft",
                    "draft_source": "attacker-controlled",
                }
            ]
        }
    )
    assert invalid_send is None and error is not None

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
            text="结束演示，生成摘要，准备Outlook草稿，审核后发送。",
            steps=[
                {"name": "presentation_end_slideshow"},
                {"name": "office_generate_presentation_summary", "language": "zh"},
                {
                    "name": "outlook_create_summary_draft",
                    "language": "zh",
                    "summary_source": "latest",
                },
                {
                    "name": "outlook_send_approved_draft",
                    "draft_source": "latest_verified",
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
        asyncio.run(run_task_with_approvals(task_id, expected_approvals=2))
    finally:
        office_sequence.execute_office_tool_call = original_sequence_executor

    task = client.get(f"/agent/tasks/{task_id}").json()
    assert task["status"] == "completed"
    assert [step["status"] for step in task["steps"]] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    outlook_draft = task["steps"][-2]["result"]["data"]
    assert outlook_draft["outlook_draft_created"] is True
    assert outlook_draft["outlook_draft_verified"] is True
    assert outlook_draft["sender_account_email"] == "jiangdizhao1@outlook.com"
    assert outlook_draft["recipient_email"] == "jiangdizhao@gmail.com"
    assert outlook_draft["sent"] is False

    outlook_send = task["steps"][-1]["result"]["data"]
    assert outlook_send["draft_notice_removed"] is True
    assert outlook_send["send_invoked"] is True
    assert outlook_send["sent"] is True
    assert outlook_send["approval_gated_email_send_enabled"] is True
    assert outlook_send["unrestricted_email_send_enabled"] is False

    interpreter = (
        BACKEND_DIR.parent
        / "ui"
        / "smart-office-ui"
        / "src"
        / "voice"
        / "realtimeOfficeInterpreter.ts"
    ).read_text(encoding="utf-8")
    assert "name: 'office_plan'" in interpreter
    assert "outlook_create_summary_draft" in interpreter
    assert "outlook_send_approved_draft" in interpreter
    assert "fixed signed-in Classic Outlook sender account" in interpreter
    assert "second Backend approval" in interpreter
    assert "removes the sentence saying the message is only a draft" in interpreter
    assert "gmail_create_summary_draft" not in interpreter

    outlook_draft_source = (BACKEND_DIR / "app" / "outlook_drafts.py").read_text(
        encoding="utf-8"
    )
    outlook_send_source = (BACKEND_DIR / "app" / "outlook_send.py").read_text(
        encoding="utf-8"
    )
    assert 'Dispatch("Outlook.Application")' in outlook_draft_source
    assert "SendUsingAccount" in outlook_draft_source
    assert "mail.Save()" in outlook_draft_source
    assert "GetItemFromID" in outlook_draft_source
    assert ".Send()" not in outlook_draft_source
    assert "DRAFT_ONLY_NOTICE_ZH" in outlook_send_source
    assert "DRAFT_ONLY_NOTICE_EN" in outlook_send_source
    assert "verified_mail.Send()" in outlook_send_source
    assert "draft_notice_removed" in outlook_send_source
    assert "jiangdizhao1@outlook.com" in (
        BACKEND_DIR / "app" / "presentation_config.py"
    ).read_text(encoding="utf-8")
    assert "jiangdizhao@gmail.com" in (
        BACKEND_DIR / "app" / "presentation_config.py"
    ).read_text(encoding="utf-8")

    print(
        "PASS: Phase 3 Gate 3-5 validates bounded device control, local summaries, "
        "first-approved Classic Outlook draft creation, second-approved fixed-recipient "
        "sending after removal of the draft-only notice, visitor denial, and prohibition "
        "of unrestricted email sending."
    )


if __name__ == "__main__":
    main()
