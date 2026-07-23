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
        raise ValueError(f"Invalid email address in Smart Office recipient file: {value!r}")
    return email


@dataclass(frozen=True)
class EmailRecipient:
    key: str
    name: str
    email: str

    def public_dict(self) -> dict[str, str]:
        return {"key": self.key, "name": self.name, "email": self.email}


@dataclass(frozen=True)
class EmailRecipientDirectory:
    config_path: Path
    default_recipient_key: str
    recipients: tuple[EmailRecipient, ...]

    def resolve(self, key: str | None = None) -> EmailRecipient:
        requested_key = _normalise_recipient_key(key or self.default_recipient_key)
        for recipient in self.recipients:
            if recipient.key == requested_key:
                return recipient
        available = ", ".join(recipient.key for recipient in self.recipients)
        raise ValueError(
            f"Unknown Smart Office email recipient key: {requested_key}. Available: {available}."
        )

    def public_catalog(self) -> list[dict[str, str]]:
        return [recipient.public_dict() for recipient in self.recipients]


def _recipient_from_value(key: str, value: Any) -> EmailRecipient:
    normalised_key = _normalise_recipient_key(key)
    if isinstance(value, str):
        name = normalised_key.replace("_", " ").replace("-", " ").title()
        email = value
    elif isinstance(value, dict):
        unexpected = set(value) - {"name", "email"}
        if unexpected:
            raise ValueError(
                f"Recipient {normalised_key!r} contains unsupported fields: {sorted(unexpected)}."
            )
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


def load_email_recipient_directory(path: Path) -> EmailRecipientDirectory:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Smart Office email recipient file was not found: {resolved}")

    try:
        parsed = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Smart Office email recipient file is not valid JSON: {resolved}: {exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError("Smart Office email recipient file must contain one JSON object.")
    unexpected = set(parsed) - {"default_recipient_key", "recipients"}
    if unexpected:
        raise ValueError(
            f"Smart Office email recipient file contains unsupported fields: {sorted(unexpected)}."
        )

    raw_recipients = parsed.get("recipients")
    if not isinstance(raw_recipients, dict) or not raw_recipients:
        raise ValueError("Smart Office email recipient file must contain a non-empty recipients object.")

    catalog: dict[str, EmailRecipient] = {}
    seen_emails: dict[str, str] = {}
    for raw_key, value in raw_recipients.items():
        recipient = _recipient_from_value(str(raw_key), value)
        if recipient.key in catalog:
            raise ValueError(f"Duplicate recipient key after normalization: {recipient.key}.")
        folded_email = recipient.email.casefold()
        if folded_email in seen_emails:
            raise ValueError(
                f"Recipient email {recipient.email} is assigned to both "
                f"{seen_emails[folded_email]!r} and {recipient.key!r}."
            )
        catalog[recipient.key] = recipient
        seen_emails[folded_email] = recipient.key

    default_key = _normalise_recipient_key(str(parsed.get("default_recipient_key") or ""))
    if default_key not in catalog:
        available = ", ".join(catalog)
        raise ValueError(
            f"default_recipient_key {default_key!r} is not present in recipients. Available: {available}."
        )

    return EmailRecipientDirectory(
        config_path=resolved,
        default_recipient_key=default_key,
        recipients=tuple(catalog.values()),
    )


@dataclass(frozen=True)
class PresentationRuntimeConfig:
    presentation_path: Path
    output_directory: Path
    target_monitor_device: str
    target_monitor_number: int
    outlook_sender_email: str
    recipient_config_path: Path
    close_powerpoint_when_empty: bool = False

    @classmethod
    def from_environment(cls) -> "PresentationRuntimeConfig":
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
            recipient_config_path=_resolve_repo_path(
                os.environ.get(
                    "SMART_OFFICE_EMAIL_RECIPIENTS_FILE",
                    "config/email_recipients.json",
                )
            ),
            close_powerpoint_when_empty=os.environ.get(
                "SMART_OFFICE_CLOSE_POWERPOINT_WHEN_EMPTY",
                "false",
            ).casefold()
            in {"1", "true", "yes", "on"},
        )

    def recipient_directory(self) -> EmailRecipientDirectory:
        # Deliberately read on every call. Users can edit the file while the Backend
        # is running, and the next status/draft/send action sees the new contents.
        return load_email_recipient_directory(self.recipient_config_path)

    @property
    def email_recipients(self) -> tuple[EmailRecipient, ...]:
        return self.recipient_directory().recipients

    @property
    def default_recipient_key(self) -> str:
        return self.recipient_directory().default_recipient_key

    def resolve_recipient(self, key: str | None = None) -> EmailRecipient:
        return self.recipient_directory().resolve(key)

    @property
    def recipient_name(self) -> str:
        return self.resolve_recipient().name

    @property
    def recipient_email(self) -> str:
        return self.resolve_recipient().email

    def recipient_catalog(self) -> list[dict[str, str]]:
        return self.recipient_directory().public_catalog()

    def public_dict(self) -> dict:
        directory = self.recipient_directory()
        default_recipient = directory.resolve()
        payload = asdict(self)
        payload["presentation_path"] = str(self.presentation_path)
        payload["presentation_path_relative"] = _relative_or_absolute(self.presentation_path)
        payload["presentation_exists"] = self.presentation_path.is_file()
        payload["output_directory"] = str(self.output_directory)
        payload["output_directory_relative"] = _relative_or_absolute(self.output_directory)
        payload["output_directory_exists"] = self.output_directory.is_dir()
        payload["recipient_config_path"] = str(self.recipient_config_path)
        payload["recipient_config_path_relative"] = _relative_or_absolute(
            self.recipient_config_path
        )
        payload["recipient_config_exists"] = self.recipient_config_path.is_file()
        payload["email_recipients"] = directory.public_catalog()
        payload["default_recipient_key"] = directory.default_recipient_key
        payload["default_recipient"] = default_recipient.public_dict()
        payload["recipient_name"] = default_recipient.name
        payload["recipient_email"] = default_recipient.email
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
