from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
import smoke_conversation_proximity_contract as legacy  # noqa: E402


class _ResponseView:
    def __init__(self, response, payload: dict) -> None:
        self._response = response
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self):
        return self._response.raise_for_status()

    def __getattr__(self, name: str):
        return getattr(self._response, name)


class _LegacyExpectationClient(TestClient):
    """Keeps the old contract focused on lifecycle rather than retired copy.

    The real Phase 1 registered opening is tested immediately before the legacy
    suite. Only the obsolete exact-greeting field seen by the legacy assertions is
    adapted; every Visit, task, memory, fencing and voice check still uses the real
    application.
    """

    def post(self, url, *args, **kwargs):
        response = super().post(url, *args, **kwargs)
        body = kwargs.get("json")
        if (
            str(url).endswith("/proximity-greeting")
            and isinstance(body, dict)
            and body.get("greeting_kind") == "registered_identity"
            and response.is_success
        ):
            payload = response.json()
            name = str(body.get("display_name") or "").strip() or "visitor"
            payload["greeting"] = f"Welcome back, {name}."
            return _ResponseView(response, payload)
        return response


def verify_phase1_registered_opening() -> None:
    suffix = uuid4().hex[:10]
    conversation_id = f"phase1-registered-opening-{suffix}"
    visit_id = f"phase1-registered-visit-{suffix}"
    with TestClient(app) as client:
        response = client.post(
            f"/api/conversations/{conversation_id}/proximity-greeting",
            json=legacy.detection(
                visit_id,
                greeting_kind="registered_identity",
                identity_id=f"rico-{suffix}",
                display_name="Rico",
            ),
        )
        response.raise_for_status()
        payload = response.json()
        greeting = payload["greeting"]
        opening = payload["opening"]
        assert greeting.startswith("Welcome back, Rico.")
        assert "Digital Manager and Enterprise Solution Consultant" in greeting
        assert "virtual assistant" not in greeting.casefold()
        assert "virtual host" not in greeting.casefold()
        assert opening["reply_mode"] == "opening"
        assert opening["purpose"] == "sales_opening"
        assert opening["humour_theme"] is None
        assert opening["question_field"] == "role"
        assert "management, customer communication or technical delivery" in opening["text"]
        assert opening["delivery"]["style"] == "light_playful"
        assert payload["registered_return"] is True


def main() -> None:
    verify_phase1_registered_opening()
    original = legacy.TestClient
    legacy.TestClient = _LegacyExpectationClient
    try:
        legacy.main()
    finally:
        legacy.TestClient = original
    print(
        "PASS: Phase 1 registered visitors receive the compact role-first sales opening "
        "without textual humour, while the complete legacy Visit, task, memory, Realtime, "
        "VAD and remote-vision lifecycle contract remains intact."
    )


if __name__ == "__main__":
    main()
