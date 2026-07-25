from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PowerPointBootstrapResult:
    ok: bool
    already_running: bool = False
    launched: bool = False
    launch_method: str | None = None
    executable: str | None = None
    process_detected: bool = False
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


def _powerpoint_process_running() -> bool:
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


def _registry_powerpoint_paths() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []

    paths: list[Path] = []
    registry_locations = (
        (
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\powerpnt.exe",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\powerpnt.exe",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\powerpnt.exe",
        ),
    )
    for hive, key_name in registry_locations:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value, _kind = winreg.QueryValueEx(key, None)
            candidate = Path(str(value).strip('"')).expanduser()
            if candidate.is_file() and candidate not in paths:
                paths.append(candidate)
        except OSError:
            continue
    return paths


def _candidate_powerpoint_executables() -> list[Path]:
    candidates: list[Path] = []

    discovered = shutil.which("POWERPNT.EXE") or shutil.which("powerpnt.exe")
    if discovered:
        candidates.append(Path(discovered))

    candidates.extend(_registry_powerpoint_paths())

    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramW6432"),
    ]
    relative_paths = (
        r"Microsoft Office\root\Office16\POWERPNT.EXE",
        r"Microsoft Office\Office16\POWERPNT.EXE",
        r"Microsoft Office\root\Office15\POWERPNT.EXE",
        r"Microsoft Office\Office15\POWERPNT.EXE",
    )
    for root in roots:
        if not root:
            continue
        for relative in relative_paths:
            candidate = Path(root) / relative
            if candidate.is_file() and candidate not in candidates:
                candidates.append(candidate)

    return candidates


def _launch_powerpoint_desktop() -> tuple[bool, str, str | None, str | None]:
    launch_errors: list[str] = []
    for executable in _candidate_powerpoint_executables():
        try:
            subprocess.Popen(
                [str(executable)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            return True, "powerpoint_executable", str(executable), None
        except Exception as exc:
            launch_errors.append(f"{executable}: {type(exc).__name__}: {exc}")

    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", "powerpnt.exe"],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "cmd_start_powerpnt", None, None
    except Exception as exc:
        launch_errors.append(f"cmd start powerpnt.exe: {type(exc).__name__}: {exc}")

    return False, "unavailable", None, " | ".join(launch_errors)


def ensure_powerpoint_desktop_running(
    *,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.25,
) -> PowerPointBootstrapResult:
    """Ensure an interactive PowerPoint desktop process is registered in the ROT.

    A clean Windows/Office installation may not put ``POWERPNT.EXE`` on PATH.
    The bootstrap checks Office App Paths registry entries and common Click-to-Run
    locations before falling back to ``cmd start`` and direct COM activation.
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
            error=f"pywin32 is unavailable in the Backend Python: {exc}",
        )

    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    application = None
    try:
        application = _get_active_powerpoint(win32com.client)
        if application is not None:
            return PowerPointBootstrapResult(
                ok=True,
                already_running=True,
                process_detected=True,
                attempts=1,
                duration_ms=round((time.monotonic() - started_at) * 1000),
            )

        launched, launch_method, executable, launch_error = _launch_powerpoint_desktop()
        deadline = time.monotonic() + timeout_seconds
        attempts = 0

        while launched and time.monotonic() <= deadline:
            attempts += 1
            application = _get_active_powerpoint(win32com.client)
            if application is not None:
                return PowerPointBootstrapResult(
                    ok=True,
                    launched=True,
                    launch_method=launch_method,
                    executable=executable,
                    process_detected=True,
                    attempts=attempts,
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                )
            time.sleep(poll_interval_seconds)

        process_detected = _powerpoint_process_running()

        dispatch_error: str | None = None
        try:
            application = win32com.client.Dispatch("PowerPoint.Application")
            application.Visible = True
            return PowerPointBootstrapResult(
                ok=True,
                launched=True,
                launch_method="com_dispatch",
                executable=executable,
                process_detected=True,
                attempts=attempts + 1,
                duration_ms=round((time.monotonic() - started_at) * 1000),
            )
        except Exception as exc:
            dispatch_error = f"{type(exc).__name__}: {exc}"

        if process_detected:
            error = (
                "POWERPNT.EXE started, but PowerPoint COM was not registered. "
                "Open PowerPoint manually and complete Office activation, the first-run "
                "privacy/licence screens, Protected View prompts, or any modal dialog; "
                "then restart the Backend. "
                f"Direct COM activation also failed: {dispatch_error}"
            )
        else:
            error = (
                "Microsoft PowerPoint Desktop could not be started. Confirm that the "
                "desktop PowerPoint application is installed and can open manually. "
                f"Launcher error: {launch_error or 'no executable became available'}; "
                f"direct COM activation: {dispatch_error}"
            )

        return PowerPointBootstrapResult(
            ok=False,
            launched=launched,
            launch_method=launch_method,
            executable=executable,
            process_detected=process_detected,
            attempts=attempts + 1,
            duration_ms=round((time.monotonic() - started_at) * 1000),
            error=error,
        )
    finally:
        application = None
        pythoncom.CoUninitialize()
