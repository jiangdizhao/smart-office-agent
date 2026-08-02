from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ["SMART_OFFICE_ADMIN_PASSWORD"] = "contract-admin-password"
os.environ.pop("SMART_OFFICE_ADMIN_PASSWORD_HASH", None)

from app.main import app  # noqa: E402
from app.result_center_auth import reset_result_center_auth_for_tests  # noqa: E402


def context_headers(token: str, visit_id: str, panel_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-SmartOffice-Visit-Id": visit_id,
        "X-SmartOffice-Panel-Instance-Id": panel_id,
    }


def main() -> None:
    reset_result_center_auth_for_tests()
    client = TestClient(app)
    visit_a = "visit-contract-a"
    visit_b = "visit-contract-b"
    panel_a = "panel-contract-a"
    panel_b = "panel-contract-b"

    denied = client.get("/api/result-center/recordings")
    assert denied.status_code == 401, denied.text

    incomplete = client.post(
        "/api/result-center/admin/login",
        json={"password": "contract-admin-password"},
    )
    assert incomplete.status_code == 422, incomplete.text

    wrong = client.post(
        "/api/result-center/admin/login",
        json={
            "password": "incorrect",
            "visit_id": visit_a,
            "panel_instance_id": panel_a,
        },
    )
    assert wrong.status_code == 401, wrong.text

    login = client.post(
        "/api/result-center/admin/login",
        json={
            "password": "contract-admin-password",
            "visit_id": visit_a,
            "panel_instance_id": panel_a,
        },
    )
    login.raise_for_status()
    payload = login.json()
    token = str(payload["access_token"])
    assert token
    assert payload["visit_id"] == visit_a
    assert payload["panel_instance_id"] == panel_a
    assert payload["expires_in_seconds"] >= 60

    status = client.get(
        "/api/result-center/admin/status",
        headers=context_headers(token, visit_a, panel_a),
    )
    status.raise_for_status()
    assert status.json()["authenticated"] is True

    bearer_access = client.get(
        "/api/result-center/recordings",
        headers=context_headers(token, visit_a, panel_a),
    )
    bearer_access.raise_for_status()
    assert bearer_access.json()["ok"] is True

    wrong_visit = client.get(
        "/api/result-center/recordings",
        headers=context_headers(token, visit_b, panel_a),
    )
    assert wrong_visit.status_code == 401, wrong_visit.text

    wrong_panel = client.get(
        "/api/result-center/recordings",
        headers=context_headers(token, visit_a, panel_b),
    )
    assert wrong_panel.status_code == 401, wrong_panel.text

    # A new Visit from the same client address must not inherit the old Visit's
    # administrator session.
    passive_new_visit = client.get(
        "/api/result-center/recordings",
        headers={
            "X-SmartOffice-Visit-Id": visit_b,
            "X-SmartOffice-Panel-Instance-Id": panel_b,
        },
    )
    assert passive_new_visit.status_code == 401, passive_new_visit.text

    logout = client.post(
        "/api/result-center/admin/logout",
        headers=context_headers(token, visit_a, panel_a),
    )
    logout.raise_for_status()
    assert logout.json()["logged_out"] is True

    denied_again = client.get(
        "/api/result-center/recordings",
        headers=context_headers(token, visit_a, panel_a),
    )
    assert denied_again.status_code == 401, denied_again.text

    manager = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "display"
        / "multiScreenWindowManager.ts"
    ).read_text(encoding="utf-8")
    semantic = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "interaction"
        / "semanticInteractionInterpreter.ts"
    ).read_text(encoding="utf-8")
    protected_app = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "interaction"
        / "ProtectedResultCenterApp.tsx"
    ).read_text(encoding="utf-8")
    command_bridge = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "interaction"
        / "interactionPanelCommandBridge.ts"
    ).read_text(encoding="utf-8")
    voice_interpreter = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "interaction"
        / "interactionVoiceCommandInterpreter.ts"
    ).read_text(encoding="utf-8")
    coordinator = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "voice"
        / "preemptiveTurnCoordinator.ts"
    ).read_text(encoding="utf-8")
    liveness = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "voice"
        / "realtimeLivenessPatch.ts"
    ).read_text(encoding="utf-8")

    assert "panelInstanceId" in manager
    assert "我想登记" in manager
    assert "visitor_service_intent_classification" in semantic
    assert "管理员密码" in protected_app
    assert "sessionStorage" not in protected_app
    assert "visit_id: visitId" in protected_app
    assert "panel_instance_id: panelInstanceId" in protected_app
    assert "stop_save_summarize" in command_bridge
    assert "play_latest_recording" in command_bridge
    assert "action: 'start'" in voice_interpreter
    assert "导出" in voice_interpreter
    assert "smartoffice:realtime-vad-speech-started" in coordinator
    assert "/cancel" in coordinator
    assert "takeLatestUtterance" in coordinator
    assert "recoverToReady" in coordinator
    assert "preferLatestUtterance" not in coordinator
    assert "utteranceQueue.splice(0)" in liveness
    assert "vad-max-utterance-forced-boundary" in liveness

    print(
        "PASS: result-center authorization is Visit/panel scoped; new Visits require a "
        "fresh password; recording/results panel voice actions, bounded VAD liveness, "
        "and capacity-one latest-command preemption contracts are present."
    )


if __name__ == "__main__":
    main()
