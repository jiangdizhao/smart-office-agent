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


def main() -> None:
    reset_result_center_auth_for_tests()
    client = TestClient(app)

    denied = client.get("/api/result-center/recordings")
    assert denied.status_code == 401, denied.text

    wrong = client.post(
        "/api/result-center/admin/login",
        json={"password": "incorrect"},
    )
    assert wrong.status_code == 401, wrong.text

    login = client.post(
        "/api/result-center/admin/login",
        json={"password": "contract-admin-password"},
    )
    login.raise_for_status()
    payload = login.json()
    token = str(payload["access_token"])
    assert token
    assert payload["expires_in_seconds"] >= 60

    status = client.get(
        "/api/result-center/admin/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    status.raise_for_status()
    assert status.json()["authenticated"] is True

    bearer_access = client.get(
        "/api/result-center/recordings",
        headers={"Authorization": f"Bearer {token}"},
    )
    bearer_access.raise_for_status()
    assert bearer_access.json()["ok"] is True

    # Passive audio/CSV browser requests cannot set Authorization themselves.
    # The successful login grants this same TestClient address an equal-expiry lease.
    passive_access = client.get("/api/result-center/recordings")
    passive_access.raise_for_status()

    logout = client.post(
        "/api/result-center/admin/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    logout.raise_for_status()

    denied_again = client.get("/api/result-center/recordings")
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

    assert "我想登记" in manager
    assert "visitor_service_intent_classification" in semantic
    assert "'contact'" in semantic
    assert "'recording'" in semantic
    assert "'transcript'" in semantic
    assert "'results'" in semantic
    assert "管理员密码" in protected_app
    assert "/api/result-center/admin/login" in protected_app
    assert "Authorization" in protected_app

    print(
        "PASS: visitor-service semantic intent and protected result-center "
        "administrator authentication contracts are present."
    )


if __name__ == "__main__":
    main()
