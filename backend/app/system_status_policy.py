from __future__ import annotations

from app.models import ToolResult
from app.tools.system_controller import _read_volume


def get_lightweight_system_control_status() -> ToolResult:
    """Return routine system status without probing monitor brightness.

    Brightness providers (DDC/CI, VCP and WMI) are hardware-specific and may block
    or fail on external-monitor topologies. They are therefore invoked only by an
    explicit brightness action, never by email, Outlook, recipient, summary,
    PowerPoint, volume, or general Office status workflows.
    """

    volume = _read_volume()
    brightness = {
        "available": False,
        "brightness_percent": None,
        "instances": [],
        "provider": None,
        "error": None,
        "deferred": True,
        "message": "Brightness probing is deferred until an explicit brightness command.",
    }
    return ToolResult(
        tool_name="system_get_status",
        ok=bool(volume.get("available")),
        message="Windows volume status inspected; brightness probing was deferred.",
        data={
            "execution_mode": "real",
            "volume": volume,
            "brightness": brightness,
            "volume_percent": volume.get("volume_percent"),
            "brightness_percent": None,
            "brightness_provider": None,
            "brightness_probe_deferred": True,
            "requested_state": {},
        },
    )


def install_lightweight_system_status_policy() -> None:
    """Install the no-incidental-brightness policy in the Office action module."""

    from app import office_actions

    office_actions.get_system_control_status = get_lightweight_system_control_status
