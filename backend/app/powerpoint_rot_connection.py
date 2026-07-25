from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from typing import Any, Iterator

import app.tools.presentation_controller as presentation_controller_module


class RotResilientPowerPointController(
    presentation_controller_module.PowerPointController
):
    """Reconnect to a running PowerPoint instance when ROT lookup is unavailable.

    Some PowerPoint installations launch and automate correctly through ``Dispatch``
    but do not expose ``PowerPoint.Application`` through ``GetActiveObject``. In that
    state the original controller could execute an action successfully and then fail
    its independent verification pass. The fallback below is deliberately bounded:
    read/control calls use ``Dispatch`` only when a POWERPNT.EXE process already
    exists, so a status query never launches PowerPoint by itself.
    """

    @staticmethod
    def _powerpoint_process_running() -> bool:
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
    def _com_application(self, *, create: bool) -> Iterator[Any | None]:
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
                application = win32com.client.GetActiveObject(
                    "PowerPoint.Application"
                )
            except Exception:
                if create:
                    application = win32com.client.Dispatch(
                        "PowerPoint.Application"
                    )
                elif self._powerpoint_process_running():
                    try:
                        application = win32com.client.Dispatch(
                            "PowerPoint.Application"
                        )
                    except Exception:
                        application = None
            yield application
        finally:
            application = None
            pythoncom.CoUninitialize()


def install_powerpoint_rot_fallback() -> None:
    """Install the ROT-resilient singleton once for all controller wrapper calls."""

    current = presentation_controller_module.presentation_controller
    if isinstance(current, RotResilientPowerPointController):
        return
    presentation_controller_module.presentation_controller = (
        RotResilientPowerPointController(current.config)
    )
