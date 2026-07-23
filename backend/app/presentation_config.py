from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
_RECIPIENT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _resolve_repo_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (REPO_ROOT / candidate).resolve()


def _normalise_recipient_key(value: str) -> str:
    key = value.strip().casefold()
    if not _RECIPIENT_KEY_PATTERN.fullmatch(key):
        raise ValueError(
            "Email recipient keys must use 1-32 lowercase letters, digits, underscores, or hyphens."
        )
    return key


def _validate_email(value: str) -> str:
    email = value.strip()
    if not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError(f"Invalid email address in Smart Office recipient allowlist: {value!r}")
    return email


@dataclass(frozen=True)
class EmailRecipient:
    key: str
    name: str
    email: str

    def public_dict(self) -> dict[str, str]:
        return {"key": self.key, "name": self.name, "email": self.email}


def _recipient_from_value(key: str, value: Any) -> EmailRecipient:
    normalised_key = _normalise_recipient_key(key)
    if isinstance(value, str):
        name = normalised_key.replace("_", " ").replace("-", " ").title()
        email = value
    elif isinstance(value, dict):
        name = str(value.get("name") or normalised_key).strip()
        email = str(value.get("email") or "").strip()
    else:
        raise ValueError(
            f"Recipient {normalised_key!r} must be an email string or an object with name/email."
        )
    if not name:
        raise ValueError(f"Recipient {normalised_key!r} must have a display name.")
    return EmailRecipient(
        key=normalised_key,
        name=name[:120],
        email=_validate_email(email),
    )


def _email_recipients_from_environment() -> tuple[EmailRecipient, ...]:
    legacy_name = os.environ.get("SMART_OFFICE_DEMO_RECIPIENT_NAME", "Rico").strip() or "Rico"
    legacy_email = os.environ.get(
        "SMART_OFFICE_DEMO_RECIPIENT_EMAIL",
        "jiangdizhao@gmail.com",
    ).strip()
    catalog: dict[str, EmailRecipient] = {
        "rico": EmailRecipient(
            key="rico",
            name=legacy_name,
            email=_validate_email(legacy_email),
        )
    }

    raw = os.environ.get("SMART_OFFICE_EMAIL_RECIPIENTS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "SMART_OFFICE_EMAIL_RECIPIENTS_JSON must be valid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError("SMART_OFFICE_EMAIL_RECIPIENTS_JSON must be a JSON object.")
        for raw_key, value in parsed.items():
            recipient = _recipient_from_value(str(raw_key), value)
            catalog[recipient.key] = recipient

    return tuple(catalog[key] for key in sorted(catalog))


@dataclass(frozen=True)
class PresentationRuntimeConfig:
    presentation_path: Path
    output_directory: Path
    target_monitor_device: str
    target_monitor_number: int
    outlook_sender_email: str
    email_recipients: tuple[EmailRecipient, ...]
    default_recipient_key: str
    close_powerpoint_when_empty: bool = False

    @classmethod
    def from_environment(cls) -> "PresentationRuntimeConfig":
        recipients = _email_recipients_from_environment()
        default_key = _normalise_recipient_key(
            os.environ.get("SMART_OFFICE_DEFAULT_RECIPIENT_KEY", "rico")
        )
        if default_key not in {recipient.key for recipient in recipients}:
            raise ValueError(
                f"SMART_OFFICE_DEFAULT_RECIPIENT_KEY {default_key!r} is not present in "
                "SMART_OFFICE_EMAIL_RECIPIENTS_JSON."
            )
        return cls(
            presentation_path=_resolve_repo_path(
                os.environ.get("SMART_OFFICE_DEMO_PPT", "demo_files/Loss.pptx")
            ),
            output_directory=_resolve_repo_path(
                os.environ.get("SMART_OFFICE_OUTPUT_DIR", "demo_files/LOG")
            ),
            target_monitor_device=os.environ.get(
                "SMART_OFFICE_PRESENTATION_MONITOR_DEVICE",
                r"\\.\DISPLAY2",
            ),
            target_monitor_number=max(
                1,
                int(os.environ.get("SMART_OFFICE_PRESENTATION_MONITOR_NUMBER", "2")),
            ),
            outlook_sender_email=os.environ.get(
                "SMART_OFFICE_OUTLOOK_SENDER_EMAIL",
                "jiangdizhao1@outlook.com",
            ).strip(),
            email_recipients=recipients,
            default_recipient_key=default_key,
            close_powerpoint_when_empty=os.environ.get(
                "SMART_OFFICE_CLOSE_POWERPOINT_WHEN_EMPTY",
                "false",
            ).casefold()
            in {"1", "true", "yes", "on"},
        )

    def resolve_recipient(self, key: str | None = None) -> EmailRecipient:
        requested_key = _normalise_recipient_key(key or self.default_recipient_key)
        for recipient in self.email_recipients:
            if recipient.key == requested_key:
                return recipient
        available = ", ".join(recipient.key for recipient in self.email_recipients)
        raise ValueError(
            f"Unknown Smart Office email recipient key: {requested_key}. Available: {available}."
        )

    @property
    def recipient_name(self) -> str:
        return self.resolve_recipient().name

    @property
    def recipient_email(self) -> str:
        return self.resolve_recipient().email

    def recipient_catalog(self) -> list[dict[str, str]]:
        return [recipient.public_dict() for recipient in self.email_recipients]

    def public_dict(self) -> dict:
        payload = asdict(self)
        payload["presentation_path"] = str(self.presentation_path)
        payload["presentation_path_relative"] = _relative_or_absolute(self.presentation_path)
        payload["presentation_exists"] = self.presentation_path.is_file()
        payload["output_directory"] = str(self.output_directory)
        payload["output_directory_relative"] = _relative_or_absolute(self.output_directory)
        payload["output_directory_exists"] = self.output_directory.is_dir()
        payload["email_recipients"] = self.recipient_catalog()
        payload["default_recipient"] = self.resolve_recipient().public_dict()
        payload["recipient_name"] = self.recipient_name
        payload["recipient_email"] = self.recipient_email
        payload["email_send_enabled"] = False
        payload["approval_gated_email_send_enabled"] = True
        payload["unrestricted_email_send_enabled"] = False
        payload["automation"] = "powerpoint_com"
        return payload


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


presentation_config = PresentationRuntimeConfig.from_environment()
