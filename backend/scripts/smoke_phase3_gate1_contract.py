from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402


CURRENT_OR_LATER_RUNTIME_PHASES = {
    "m3a_fusion_phase_3_gate_3_5",
    "preemptive_visit_orchestration",
    "phase1_sales_runtime_with_preemptive_visit_orchestration",
}


def main() -> None:
    client = TestClient(app)

    health = client.get("/")
    health.raise_for_status()
    health_payload = health.json()
    # The root phase describes the current whole-product runtime, not the historical
    # phase in which this Gate 1 contract was introduced. Accept the original phase
    # and later Visit/sales-orchestration phases while continuing to assert the
    # actual bounded presentation capabilities below.
    assert health_payload["phase"] in CURRENT_OR_LATER_RUNTIME_PHASES
    assert health_payload["capabilities"]["presentation_controller"] is True
    assert health_payload["capabilities"]["presentation_state_verifier"] is True
    assert health_payload["capabilities"]["presentation_execution_via_turn"] is True
    # Gate 3-5 adds a separate bounded Office workflow endpoint. The legacy
    # general-purpose /agent/turn path must remain unable to execute arbitrary
    # Office actions.
    assert health_payload["capabilities"]["general_office_execution_via_turn"] is False

    status = client.get("/api/presentation/status")
    status.raise_for_status()
    status_payload = status.json()
    assert status_payload["phase"] == "m3a_fusion_phase_3_gate_1"
    assert status_payload["config"]["presentation_path"].endswith(
        os.path.join("demo_files", "Loss.pptx")
    )
    assert status_payload["config"]["output_directory"].endswith(
        os.path.join("demo_files", "LOG")
    )
    assert status_payload["config"]["recipient_name"] == "Rico"
    assert status_payload["config"]["recipient_email"] == "jiangdizhao@gmail.com"
    assert status_payload["config"]["email_send_enabled"] is False
    assert status_payload["status"]["tool_name"] == "presentation_get_status"

    invalid_goto = client.post(
        "/api/presentation/slideshow/goto",
        json={"slide_number": 0},
    )
    assert invalid_goto.status_code == 422

    invalid_volume = client.post(
        "/api/office/system/volume",
        json={"percent": 101},
    )
    assert invalid_volume.status_code == 422, (
        invalid_volume.status_code,
        invalid_volume.text,
    )

    blocked_turn = client.post(
        "/agent/turn",
        json={
            "conversation_id": "phase3-gate1-contract",
            "text": "Open the presentation and start slide show",
            "language": "en",
            "input_source": "text",
            "actor_context": {"type": "employee"},
            "execute": True,
        },
    )
    blocked_turn.raise_for_status()
    blocked_payload = blocked_turn.json()
    assert blocked_payload["requires_approval"] is False, blocked_payload
    assert blocked_payload["tool_result"] is None, blocked_payload
    assert blocked_payload["verification_result"] is None, blocked_payload
    assert blocked_payload["presentation_status"] is None, blocked_payload

    if blocked_payload["route"] == "office_action_blocked":
        assert blocked_payload["task_id"] is None, blocked_payload
        assert blocked_payload["task_status"] is None, blocked_payload
    else:
        # Later runtimes may retain a non-bounded legacy request as a plan-only task
        # for observability. This remains safe only when execute=False on the stored
        # task and no tool result, verification result or presentation state exists.
        assert blocked_payload["route"] == "office_planned_task", blocked_payload
        assert "no real Office action was executed" in blocked_payload["spoken_text"], blocked_payload
        task_id = str(blocked_payload["task_id"] or "")
        assert task_id, blocked_payload
        task_response = client.get(f"/agent/tasks/{task_id}")
        task_response.raise_for_status()
        task_payload = task_response.json()
        assert task_payload["execute"] is False, task_payload
        assert all(step.get("result") is None for step in task_payload.get("steps", [])), task_payload
        assert all(
            step.get("status") not in {"running", "verifying", "succeeded"}
            for step in task_payload.get("steps", [])
        ), task_payload

    print(
        "PASS: Gate 1 presentation API and safety contracts remain available in the "
        "current runtime without enabling arbitrary Office execution through /agent/turn."
    )


if __name__ == "__main__":
    main()
