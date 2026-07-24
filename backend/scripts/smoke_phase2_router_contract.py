from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402


def post_turn(client: TestClient, *, conversation_id: str, text: str, actor: str) -> dict:
    response = client.post(
        "/agent/turn",
        json={
            "conversation_id": conversation_id,
            "text": text,
            "language": "zh",
            "input_source": "text",
            "actor_context": {"type": actor},
        },
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    client = TestClient(app)

    status = client.get("/agent/turn/status")
    status.raise_for_status()
    status_payload = status.json()
    assert status_payload["phase"] == "m3a_fusion_phase_3_gate_2b"
    assert status_payload["task_creation_enabled"] is True
    assert status_payload["office_execution_enabled"] is False
    assert status_payload["presentation_execution_enabled"] is True
    assert status_payload["compound_presentation_execution_enabled"] is True
    assert "reception_knowledge" in status_payload["routes"]
    assert "office_planned_task" in status_payload["routes"]
    assert "approval_action" in status_payload["routes"]

    reception_status = client.get("/api/reception/status")
    reception_status.raise_for_status()
    assert reception_status.json()["entry_count"] >= 5

    visitor_reception = post_turn(
        client,
        conversation_id="phase2-visitor",
        text="请介绍一下你们的解决方案",
        actor="visitor",
    )
    assert visitor_reception["route"] == "reception_knowledge"
    assert visitor_reception["permission_decision"] == "allowed"
    assert visitor_reception["source_ids"]
    assert visitor_reception["content_url"]

    content_page = client.get(visitor_reception["content_url"])
    content_page.raise_for_status()
    assert "Smart Office Reception Content" in content_page.text

    visitor_office = post_turn(
        client,
        conversation_id="phase2-visitor",
        text="打开 PowerPoint",
        actor="visitor",
    )
    assert visitor_office["route"] == "office_direct"
    assert visitor_office["permission_decision"] == "denied"
    assert visitor_office["task_id"] is None

    employee_direct = post_turn(
        client,
        conversation_id="phase2-employee-direct",
        text="打开 PowerPoint",
        actor="employee",
    )
    assert employee_direct["route"] == "office_direct"
    assert employee_direct["permission_decision"] == "allowed"
    assert employee_direct["task_id"] is None
    assert "没有执行" in employee_direct["spoken_text"]

    employee_planned = post_turn(
        client,
        conversation_id="phase2-employee-plan",
        text="准备 Teams 会议并打开 PowerPoint 演示",
        actor="employee",
    )
    assert employee_planned["route"] == "office_planned_task"
    assert employee_planned["permission_decision"] == "allowed"
    assert employee_planned["task_id"]
    assert employee_planned["approval_required"] is True

    task = client.get(f"/agent/tasks/{employee_planned['task_id']}")
    task.raise_for_status()
    assert task.json()["execute"] is False

    cancel = post_turn(
        client,
        conversation_id="phase2-employee-plan",
        text="取消任务",
        actor="employee",
    )
    assert cancel["route"] == "approval_action"
    assert cancel["approval_action"] == "cancel"
    assert cancel["task_id"] == employee_planned["task_id"]

    cancelled_task = client.get(f"/agent/tasks/{employee_planned['task_id']}")
    cancelled_task.raise_for_status()
    assert cancelled_task.json()["status"] == "cancelled"

    no_active_approval = post_turn(
        client,
        conversation_id="phase2-no-task",
        text="同意",
        actor="employee",
    )
    assert no_active_approval["route"] == "approval_action"
    assert no_active_approval["task_id"] is None
    assert "没有" in no_active_approval["spoken_text"]

    conversation = client.get("/agent/conversations/phase2-visitor")
    conversation.raise_for_status()
    assert conversation.json()["conversation"]["actor_type"] == "visitor"

    print("PASS: Phase 2 routing, grounded reception, and permission gates remain healthy under Gate 2B.")


if __name__ == "__main__":
    main()
