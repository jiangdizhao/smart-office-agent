from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from typing import Any, Iterator

import app.tools.presentation_controller as presentation_controller_module


def powerpoint_process_running() -> bool:
    """Return whether an interactive PowerPoint desktop process already exists."""

    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq POWERPNT.EXE", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "POWERPNT.EXE" in (result.stdout or "").upper()
    except Exception:
        return False


@contextmanager
def connect_powerpoint_application(*, create: bool) -> Iterator[Any | None]:
    """Connect to PowerPoint without launching it during read-only status checks.

    ``GetActiveObject`` is preferred, but some Office installations never expose the
    running PowerPoint application through the ROT. On those machines ``Dispatch``
    still reconnects correctly. A non-creating call therefore uses ``Dispatch`` only
    when POWERPNT.EXE is already present; a creating call may start PowerPoint.
    """

    try:
        import pythoncom
        import win32com.client
    except ImportError:
        yield None
        return

    pythoncom.CoInitialize()
    application = None
    try:
        try:
            application = win32com.client.GetActiveObject("PowerPoint.Application")
        except Exception:
            if create or powerpoint_process_running():
                try:
                    application = win32com.client.Dispatch("PowerPoint.Application")
                except Exception:
                    application = None
        yield application
    finally:
        application = None
        pythoncom.CoUninitialize()


class RotResilientPowerPointController(
    presentation_controller_module.PowerPointController
):
    """PowerPoint controller that tolerates a missing ROT registration."""

    @staticmethod
    def _powerpoint_process_running() -> bool:
        return powerpoint_process_running()

    @contextmanager
    def _com_application(self, *, create: bool) -> Iterator[Any | None]:
        with connect_powerpoint_application(create=create) as application:
            yield application


def install_powerpoint_rot_fallback() -> None:
    """Install the ROT-resilient singleton once for all controller wrapper calls."""

    current = presentation_controller_module.presentation_controller
    if isinstance(current, RotResilientPowerPointController):
        return
    presentation_controller_module.presentation_controller = (
        RotResilientPowerPointController(current.config)
    )
