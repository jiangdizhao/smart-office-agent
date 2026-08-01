from __future__ import annotations

_installed = False


def install_desktop_integration_wrappers() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    # office_actions imports the draft function directly, so patch both the source
    # module and the already imported consumer. The wrapper retains a private alias
    # to the original implementation and therefore does not recurse.
    from app import office_actions, outlook_drafts
    from app.tools.outlook_desktop_actions import (
        create_outlook_summary_draft_on_content_display,
    )

    outlook_drafts.create_outlook_summary_draft = (
        create_outlook_summary_draft_on_content_display
    )
    office_actions.create_outlook_summary_draft = (
        create_outlook_summary_draft_on_content_display
    )
