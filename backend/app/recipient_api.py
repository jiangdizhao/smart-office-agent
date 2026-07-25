from __future__ import annotations

from fastapi import APIRouter

from app.presentation_config import presentation_config

router = APIRouter(tags=["office-recipients"])


@router.get("/api/office/recipients")
def office_recipients() -> dict:
    """Return only the editable recipient directory.

    This endpoint deliberately performs no PowerPoint, volume, brightness, WMI,
    DDC/CI, or Outlook COM inspection. Email recipient resolution must remain
    available even when an unrelated device-control provider is slow or unsupported.
    """

    directory, config_error = presentation_config.recipient_directory_status()
    catalog = directory.public_catalog() if directory else []
    default_key = directory.default_recipient_key if directory else None
    return {
        "ok": directory is not None,
        "recipient_config_path": str(presentation_config.recipient_config_path),
        "recipient_config_exists": presentation_config.recipient_config_path.is_file(),
        "recipient_config_error": config_error,
        "default_recipient_key": default_key,
        "recipient_catalog": catalog,
        "allowed_recipient_keys": [item["key"] for item in catalog],
        "recipient_file_hot_reload_enabled": True,
        "brightness_checked": False,
        "powerpoint_checked": False,
        "outlook_com_checked": False,
    }
