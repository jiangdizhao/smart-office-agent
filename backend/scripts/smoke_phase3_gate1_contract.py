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
    assert health_payload["phase"] == "m3a_fusion_phase_3_gate_2a"
    assert health_payload["capabilities"]["presentation_controller"] is True
    assert health_payload["capabilities"]["presentation_state_verifier"] is True
    assert health_payload["capabilities"]["presentation_execution_via_turn"] is True
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

    unconfirmed_close = client.post(
        "/api/presentation/close",
        json={"confirmed": False},
    )
    assert unconfirmed_close.status_code == 409

    # Linux CI cannot run PowerPoint COM. The endpoint must still return a
    # structured unsupported/failure result instead of crashing or exposing an
    # unrestricted shell fallback.
    if os.name != "nt":
        open_result = client.post("/api/presentation/open")
        open_result.raise_for_status()
        open_payload = open_result.json()
        assert open_payload["ok"] is False
        assert open_payload["tool_result"]["tool_name"] == "presentation_open_configured"
        assert open_payload["verification_result"]["ok"] is False

    turn_status = client.get("/agent/turn/status")
    turn_status.raise_for_status()
    assert turn_status.json()["office_execution_enabled"] is False
    assert turn_status.json()["presentation_execution_enabled"] is True

    print("PASS: Gate 1 presentation API and safety contracts remain available under Gate 2A.")


if __name__ == "__main__":
    main()
