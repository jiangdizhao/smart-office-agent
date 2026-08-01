from __future__ import annotations

import os

_installed = False


def install_desktop_integration_wrappers() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    # This exhibition PC has Windows display order 3 / 1 / 2 from left to right.
    # Force DISPLAY2 even when an older PowerShell session still contains a stale
    # monitor override; otherwise applications can be moved to the wrong screen.
    os.environ["SMART_OFFICE_CONTENT_MONITOR_DEVICE"] = r"\\.\DISPLAY2"

    from app import office_actions, office_sequence, outlook_drafts
    from app.tools.outlook_desktop_actions import (
        create_outlook_summary_draft_on_content_display,
    )

    outlook_drafts.create_outlook_summary_draft = (
        create_outlook_summary_draft_on_content_display
    )
    office_actions.create_outlook_summary_draft = (
        create_outlook_summary_draft_on_content_display
    )

    office_sequence._ACTION_TITLES.setdefault(
        "presentation_close",
        "Close PowerPoint without saving changes",
    )

    if os.getenv("SMART_OFFICE_WORKER_CHILD") != "1":
        from app import office_worker_process
        from app.desktop_worker_bootstrap import (
            desktop_integrated_office_worker_loop,
        )

        original_timeout = office_worker_process._timeout_seconds
        office_worker_process._timeout_seconds = lambda: max(
            35.0,
            original_timeout(),
        )
        office_worker_process._worker_loop = desktop_integrated_office_worker_loop
