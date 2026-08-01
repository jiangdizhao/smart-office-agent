from __future__ import annotations

from typing import Literal

from app.models import ToolResult
from app.outlook_drafts import create_outlook_summary_draft as _create_outlook_summary_draft
from app.windows_window_placement import place_window_on_content_monitor


def create_outlook_summary_draft_on_content_display(
    *,
    language: Literal["zh", "en"] = "zh",
    subject: str | None = None,
    recipient_key: str | None = None,
    display: bool = True,
) -> ToolResult:
    result = _create_outlook_summary_draft(
        language=language,
        subject=subject,
        recipient_key=recipient_key,
        display=display,
    )
    if not result.ok or not display:
        return result

    clean_subject = str(result.data.get("subject") or "").strip()
    placement = place_window_on_content_monitor(
        process_names=["OUTLOOK.EXE"],
        title_keywords=[clean_subject, "Outlook"],
        timeout_seconds=10.0,
    )
    placement_ok = bool(placement.get("placement_verified"))
    return result.model_copy(
        update={
            "ok": bool(result.ok and placement_ok),
            "message": (
                f"{result.message} The draft window was moved to the content display and maximized."
                if placement_ok
                else (
                    f"{result.message} The draft exists, but its visible maximized window "
                    "was not verified on the content display."
                )
            ),
            "data": {
                **result.data,
                "outlook_window_placement": placement,
                "outlook_window_placement_verified": placement_ok,
                "content_monitor_device": (
                    placement.get("target_monitor") or {}
                ).get("device"),
                "verified": bool(
                    result.data.get("outlook_draft_verified") and placement_ok
                ),
            },
            "raw": {
                **result.raw,
                "outlook_window_placement": placement,
            },
        }
    )
