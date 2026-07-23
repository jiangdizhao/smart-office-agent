from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault(
    "SMART_OFFICE_EMAIL_RECIPIENTS_FILE",
    str(REPO_ROOT / "config" / "email_recipients.example.json"),
)

from app import office_sequence  # noqa: E402
from app.presentation_config import (  # noqa: E402
    PresentationRuntimeConfig,
    presentation_config,
)


def _write_directory(
    path: Path,
    *,
    default_key: str,
    recipients: dict[str, object],
) -> None:
    path.write_text(
        json.dumps(
            {
                "default_recipient_key": default_key,
                "recipients": recipients,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    assert presentation_config.recipient_config_path == (
        REPO_ROOT / "config" / "email_recipients.example.json"
    ).resolve()
    assert presentation_config.default_recipient_key == "rico"
    assert presentation_config.resolve_recipient("rico").email == "jiangdizhao@gmail.com"

    with TemporaryDirectory() as temporary_directory:
        config_path = Path(temporary_directory) / "email_recipients.json"
        _write_directory(
            config_path,
            default_key="rico",
            recipients={
                "rico": {"name": "Rico", "email": "jiangdizhao@gmail.com"},
            },
        )

        old_path = os.environ.get("SMART_OFFICE_EMAIL_RECIPIENTS_FILE")
        try:
            os.environ["SMART_OFFICE_EMAIL_RECIPIENTS_FILE"] = str(config_path)
            runtime_config = PresentationRuntimeConfig.from_environment()
        finally:
            if old_path is None:
                os.environ.pop("SMART_OFFICE_EMAIL_RECIPIENTS_FILE", None)
            else:
                os.environ["SMART_OFFICE_EMAIL_RECIPIENTS_FILE"] = old_path

        assert runtime_config.default_recipient_key == "rico"
        assert {item["key"] for item in runtime_config.recipient_catalog()} == {"rico"}

        # Edit the same file after the runtime config object already exists. The next
        # read must see Tom immediately, proving that recipients are not cached at startup.
        _write_directory(
            config_path,
            default_key="tom",
            recipients={
                "rico": {"name": "Rico", "email": "jiangdizhao@gmail.com"},
                "tom": {"name": "Tom", "email": "tom@example.com"},
                "supervisor": "supervisor@example.com",
            },
        )
        assert runtime_config.default_recipient_key == "tom"
        assert runtime_config.resolve_recipient("tom").name == "Tom"
        assert runtime_config.resolve_recipient("tom").email == "tom@example.com"
        assert runtime_config.resolve_recipient("supervisor").email == "supervisor@example.com"
        assert {item["key"] for item in runtime_config.recipient_catalog()} == {
            "rico",
            "supervisor",
            "tom",
        }

        original_sequence_config = office_sequence.presentation_config
        office_sequence.presentation_config = runtime_config
        try:
            draft, error = office_sequence.validate_office_plan(
                {
                    "steps": [
                        {
                            "name": "outlook_create_summary_draft",
                            "summary_source": "latest",
                            "recipient_key": "tom",
                        }
                    ]
                }
            )
            assert error is None and draft is not None
            assert draft[0].args["recipient_key"] == "tom"

            unknown, error = office_sequence.validate_office_plan(
                {
                    "steps": [
                        {
                            "name": "outlook_create_summary_draft",
                            "summary_source": "latest",
                            "recipient_key": "not_configured",
                        }
                    ]
                }
            )
            assert unknown is None and error is not None
            assert "Unknown Smart Office email recipient key" in error.message

            mismatched, error = office_sequence.validate_office_plan(
                {
                    "steps": [
                        {
                            "name": "outlook_create_summary_draft",
                            "summary_source": "latest",
                            "recipient_key": "rico",
                        },
                        {
                            "name": "outlook_send_approved_draft",
                            "draft_source": "latest_verified",
                            "recipient_key": "tom",
                        },
                    ]
                }
            )
            assert mismatched is None and error is not None
            assert "same recipient_key" in error.message
        finally:
            office_sequence.presentation_config = original_sequence_config

        _write_directory(
            config_path,
            default_key="broken",
            recipients={
                "broken": {"name": "Broken", "email": "missing-at.example.com"},
            },
        )
        try:
            runtime_config.recipient_catalog()
        except ValueError as exc:
            assert "Invalid email address" in str(exc)
        else:
            raise AssertionError("Invalid recipient email in the editable file was not rejected.")

        config_path.write_text("{not-json", encoding="utf-8")
        try:
            runtime_config.recipient_catalog()
        except ValueError as exc:
            assert "not valid JSON" in str(exc)
        else:
            raise AssertionError("Malformed recipient JSON was not rejected at runtime.")

    interpreter = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "voice"
        / "realtimeOfficeInterpreter.ts"
    ).read_text(encoding="utf-8")
    assert "recipient_key" in interpreter
    assert "recipient_catalog" in interpreter
    assert "Never put an email address in this field" in interpreter
    assert "do not pass the raw email" in interpreter

    send_source = (BACKEND_DIR / "app" / "outlook_send.py").read_text(encoding="utf-8")
    assert "selected allowlisted recipient" in send_source
    assert "no additional To, CC, or BCC recipients" in send_source

    startup_source = (BACKEND_DIR / "scripts" / "start_backend_realtime.ps1").read_text(
        encoding="utf-8"
    )
    assert "SMART_OFFICE_EMAIL_RECIPIENTS_FILE" in startup_source
    assert "email_recipients.example.json" in startup_source
    assert "Copy-Item" in startup_source
    assert "Recipient file reload: enabled" in startup_source
    assert "SMART_OFFICE_EMAIL_RECIPIENTS_JSON" not in startup_source

    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config/email_recipients.json" in gitignore

    print(
        "PASS: Outlook recipients are loaded from a local editable JSON file created "
        "from a tracked template, hot-reloaded without Backend restart, rejected when "
        "unknown or malformed, and remain sole-recipient and second-approval gated."
    )


if __name__ == "__main__":
    main()
