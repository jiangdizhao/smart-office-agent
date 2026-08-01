from __future__ import annotations

import os

_installed = False


def install_desktop_integration_wrappers() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    # office_actions imports the draft function directly, so patch both the source
    # module and the already imported consumer. The wrapper retains a private alias
    # to the original implementation and therefore does not recurse.
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

    # presentation_close is a bounded action in presentation_actions. Register its
    # human-readable title in the existing Office task builder so deterministic
    # and recovered voice commands can use the same Visit-owned task pipeline.
    office_sequence._ACTION_TITLES.setdefault(
        "presentation_close",
        "Close PowerPoint without saving changes",
    )

    # The parent broker must spawn a worker that installs the same wrappers. Do not
    # replace the loop from inside the spawned child, where doing so would recurse.
    if os.getenv("SMART_OFFICE_WORKER_CHILD") != "1":
        from app import office_worker_process
        from app.desktop_worker_bootstrap import (
            desktop_integrated_office_worker_loop,
        )

        office_worker_process._worker_loop = desktop_integrated_office_worker_loop
