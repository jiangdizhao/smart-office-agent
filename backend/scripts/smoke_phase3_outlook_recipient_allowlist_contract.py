from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.office_sequence import validate_office_plan  # noqa: E402
from app.presentation_config import (  # noqa: E402
    PresentationRuntimeConfig,
    presentation_config,
)


def main() -> None:
    assert presentation_config.default_recipient_key == "rico"
    assert presentation_config.resolve_recipient("rico").email == "jiangdizhao@gmail.com"

    old_catalog = os.environ.get("SMART_OFFICE_EMAIL_RECIPIENTS_JSON")
    old_default = os.environ.get("SMART_OFFICE_DEFAULT_RECIPIENT_KEY")
    try:
        os.environ["SMART_OFFICE_EMAIL_RECIPIENTS_JSON"] = (
            '{"tom":{"name":"Tom","email":"tom@example.com"},'
            '"supervisor":"supervisor@example.com"}'
        )
        os.environ["SMART_OFFICE_DEFAULT_RECIPIENT_KEY"] = "tom"
        expanded = PresentationRuntimeConfig.from_environment()
        assert expanded.default_recipient_key == "tom"
        assert expanded.resolve_recipient("tom").name == "Tom"
        assert expanded.resolve_recipient("tom").email == "tom@example.com"
        assert expanded.resolve_recipient("supervisor").email == "supervisor@example.com"
        assert {item["key"] for item in expanded.recipient_catalog()} == {
            "rico",
            "supervisor",
            "tom",
        }

        os.environ["SMART_OFFICE_EMAIL_RECIPIENTS_JSON"] = (
            '{"broken":{"name":"Broken","email":"missing-at.example.com"}}'
        )
        try:
            PresentationRuntimeConfig.from_environment()
        except ValueError as exc:
            assert "Invalid email address" in str(exc)
        else:
            raise AssertionError("Invalid recipient email was not rejected.")
    finally:
        if old_catalog is None:
            os.environ.pop("SMART_OFFICE_EMAIL_RECIPIENTS_JSON", None)
        else:
            os.environ["SMART_OFFICE_EMAIL_RECIPIENTS_JSON"] = old_catalog
        if old_default is None:
            os.environ.pop("SMART_OFFICE_DEFAULT_RECIPIENT_KEY", None)
        else:
            os.environ["SMART_OFFICE_DEFAULT_RECIPIENT_KEY"] = old_default

    draft, error = validate_office_plan(
        {
            "steps": [
                {
                    "name": "outlook_create_summary_draft",
                    "summary_source": "latest",
                    "recipient_key": "rico",
                }
            ]
        }
    )
    assert error is None and draft is not None
    assert draft[0].args["recipient_key"] == "rico"

    unknown, error = validate_office_plan(
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

    mismatched, error = validate_office_plan(
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
                    "recipient_key": "not_configured",
                },
            ]
        }
    )
    assert mismatched is None and error is not None

    interpreter = (
        BACKEND_DIR.parent
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

    print(
        "PASS: Outlook recipient aliases are Backend-managed, extensible through JSON "
        "configuration, rejected when unknown or malformed, and remain sole-recipient and "
        "second-approval gated."
    )


if __name__ == "__main__":
    main()
