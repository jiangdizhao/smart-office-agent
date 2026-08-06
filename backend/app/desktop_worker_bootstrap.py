from __future__ import annotations

from typing import Any


def desktop_integrated_office_worker_loop(requests: Any, responses: Any) -> None:
    """Install desktop-window wrappers inside the spawned Office worker."""

    from app.desktop_integration_bootstrap import install_desktop_integration_wrappers

    install_desktop_integration_wrappers()

    # The spawned interpreter imports office_worker_process afresh. Its private
    # loop is therefore still the original serial COM loop, not this parent-side
    # replacement, so this call does not recurse.
    from app.office_worker_process import _worker_loop

    _worker_loop(requests, responses)
