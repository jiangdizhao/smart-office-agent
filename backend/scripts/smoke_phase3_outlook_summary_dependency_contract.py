from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import ToolResult  # noqa: E402
import app.office_actions as office_actions  # noqa: E402


def main() -> None:
    saved = {
        name: getattr(office_actions, name)
        for name in (
            "get_presentation_status",
            "get_system_control_status",
            "office_artifact_status",
            "outlook_draft_status",
            "latest_summary_path",
            "generate_presentation_summary",
            "create_outlook_summary_draft",
            "get_office_status",
        )
    }

    try:
        office_actions.get_presentation_status = lambda: ToolResult(
            tool_name="presentation_get_status",
            ok=True,
            message="presentation",
            data={"presentation_open": False, "verbose_presentation": "x" * 12_000},
        )
        office_actions.get_system_control_status = lambda: ToolResult(
            tool_name="system_get_status",
            ok=True,
            message="system",
            data={
                "volume_percent": 20,
                "brightness_percent": None,
                "verbose_provider_diagnostics": "y" * 12_000,
            },
        )
        office_actions.office_artifact_status = lambda: {"latest_summary": None}
        office_actions.outlook_draft_status = lambda: {
            "outlook_draft_configured": True,
            "sender_account_email": "sender@example.com",
            "default_recipient_key": "rico",
            "recipient_key": "rico",
            "recipient_name": "Rico",
            "recipient_email": "rico@example.com",
            "recipient_catalog": [
                {"key": "rico", "name": "Rico", "email": "rico@example.com"}
            ],
            "allowed_recipient_keys": ["rico"],
        }

        ordered_status = office_actions.get_office_status()
        ordered_keys = list(ordered_status.data)
        assert ordered_keys.index("recipient_catalog") < ordered_keys.index("presentation")
        assert ordered_keys.index("allowed_recipient_keys") < ordered_keys.index("system")
        bounded_prefix = str(ordered_status.data)[:8_000]
        assert "Rico" in bounded_prefix
        assert "rico@example.com" in bounded_prefix

        calls: list[str] = []
        office_actions.latest_summary_path = lambda: None

        def fake_summary(*, language: str, task_snapshot=None):
            calls.append("summary")
            assert language == "zh"
            return ToolResult(
                tool_name="office_generate_presentation_summary",
                ok=True,
                message="summary generated",
                artifacts=["C:/demo/presentation_summary_contract.md"],
                data={
                    "summary_created": True,
                    "summary_path": "C:/demo/presentation_summary_contract.md",
                    "summary_path_relative": "demo/presentation_summary_contract.md",
                    "requested_state": {"summary_created": True},
                },
            )

        def fake_draft(*, language: str, subject, recipient_key, display: bool):
            calls.append("draft")
            assert language == "zh"
            assert recipient_key == "rico"
            assert display is True
            return ToolResult(
                tool_name="outlook_create_summary_draft",
                ok=True,
                message="draft created",
                artifacts=["C:/demo/presentation_summary_contract.md"],
                data={
                    "execution_mode": "real",
                    "requested_state": {
                        "outlook_draft_created": True,
                        "recipient_key": "rico",
                    },
                    "outlook_draft_created": True,
                    "outlook_draft_verified": True,
                    "outlook_draft_entry_id": "entry-contract",
                    "recipient_key": "rico",
                    "recipient_name": "Rico",
                    "recipient_email": "rico@example.com",
                    "sender_account_email": "sender@example.com",
                    "email_send_enabled": False,
                    "sent": False,
                },
            )

        office_actions.generate_presentation_summary = fake_summary
        office_actions.create_outlook_summary_draft = fake_draft
        office_actions.get_office_status = lambda: ToolResult(
            tool_name="office_get_status",
            ok=True,
            message="status",
            data={
                "recipient_catalog": [
                    {"key": "rico", "name": "Rico", "email": "rico@example.com"}
                ],
                "allowed_recipient_keys": ["rico"],
                "default_recipient_key": "rico",
            },
        )

        result, verification, status = office_actions.execute_office_tool_call(
            "outlook_create_summary_draft",
            {"language": "zh", "recipient_key": "rico"},
        )
        assert calls == ["summary", "draft"]
        assert result.ok is True
        assert verification.ok is True
        assert result.data["summary_prerequisite_generated"] is True
        assert status.data["recipient_key"] == "rico"
    finally:
        for name, value in saved.items():
            setattr(office_actions, name, value)

    print(
        "PASS: recipient catalog stays inside the bounded planner context and a missing "
        "presentation summary is generated before the Outlook draft is created."
    )


if __name__ == "__main__":
    main()
