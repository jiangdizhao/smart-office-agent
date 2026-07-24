from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PowerPointBootstrapResult:
    ok: bool
    already_running: bool = False
    launched: bool = False
    launch_method: str | None = None
    attempts: int = 0
    duration_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_active_powerpoint(win32com_client: Any) -> Any | None:
    try:
        return win32com_client.GetActiveObject("PowerPoint.Application")
    except Exception:
        return None


def ensure_powerpoint_desktop_running(
    *,
    timeout_seconds: float = 20.0,
    poll_interval_seconds: float = 0.25,
) -> PowerPointBootstrapResult:
    """Ensure an interactive PowerPoint desktop process is registered in the ROT.

    PowerPoint COM activation can return CO_E_SERVER_EXEC_FAILURE when Office is
    started from a reload/spawn child process. Gate 1 therefore launches the
    desktop application explicitly first, then attaches through the Running
    Object Table. The configured presentation is still opened later by the
    controlled COM controller in read-only mode.
    """

    started_at = time.monotonic()
    if os.name != "nt":
        return PowerPointBootstrapResult(
            ok=False,
            duration_ms=round((time.monotonic() - started_at) * 1000),
            error="PowerPoint desktop bootstrap is only supported on Windows.",
        )

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        return PowerPointBootstrapResult(
            ok=False,
            duration_ms=round((time.monotonic() - started_at) * 1000),
            error=f"pywin32 is unavailable: {exc}",
        )

    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    application = None
    try:
        application = _get_active_powerpoint(win32com.client)
        if application is not None:
            return PowerPointBootstrapResult(
                ok=True,
                already_running=True,
                attempts=1,
                duration_ms=round((time.monotonic() - started_at) * 1000),
            )

        launch_error: str | None = None
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", "powerpnt.exe"],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launch_method = "cmd_start_powerpnt"
        except Exception as exc:
            launch_error = str(exc)
            launch_method = "cmd_start_powerpnt"

        deadline = time.monotonic() + timeout_seconds
        attempts = 0
        while launch_error is None and time.monotonic() <= deadline:
            attempts += 1
            application = _get_active_powerpoint(win32com.client)
            if application is not None:
                return PowerPointBootstrapResult(
                    ok=True,
                    launched=True,
                    launch_method=launch_method,
                    attempts=attempts,
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                )
            time.sleep(poll_interval_seconds)

        return PowerPointBootstrapResult(
            ok=False,
            launched=launch_error is None,
            launch_method=launch_method,
            attempts=attempts,
            duration_ms=round((time.monotonic() - started_at) * 1000),
            error=launch_error
            or "PowerPoint started, but its COM object was not registered before the timeout.",
        )
    finally:
        application = None
        pythoncom.CoUninitialize()
