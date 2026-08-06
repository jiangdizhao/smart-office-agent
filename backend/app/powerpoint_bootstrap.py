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
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.25,
) -> PowerPointBootstrapResult:
    """Ensure PowerPoint COM is usable without launching a duplicate blank window.

    The normal path mirrors the known-good local command: try the ROT, then call
    ``Dispatch('PowerPoint.Application')`` immediately. Direct executable launching
    is retained only as a last-resort fallback when COM activation itself fails.
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

    process_was_running = _powerpoint_process_running()
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    application = None
    attempts = 0
    try:
        attempts += 1
        application = _get_active_powerpoint(win32com.client)
        if application is not None:
            return PowerPointBootstrapResult(
                ok=True,
                already_running=True,
                process_detected=True,
                attempts=attempts,
                duration_ms=round((time.monotonic() - started_at) * 1000),
            )

        dispatch_error: str | None = None
        attempts += 1
        try:
            application = win32com.client.Dispatch("PowerPoint.Application")
            application.Visible = -1
            return PowerPointBootstrapResult(
                ok=True,
                already_running=process_was_running,
                launched=not process_was_running,
                launch_method=(
                    "com_dispatch_existing" if process_was_running else "com_dispatch"
                ),
                process_detected=True,
                attempts=attempts,
                duration_ms=round((time.monotonic() - started_at) * 1000),
            )
        except Exception as exc:
            dispatch_error = f"{type(exc).__name__}: {exc}"

        launched, launch_method, executable, launch_error = _launch_powerpoint_desktop()
        deadline = time.monotonic() + max(0.0, timeout_seconds)

        while launched and time.monotonic() <= deadline:
            attempts += 1
            application = _get_active_powerpoint(win32com.client)
            if application is None:
                try:
                    application = win32com.client.Dispatch("PowerPoint.Application")
                except Exception:
                    application = None
            if application is not None:
                try:
                    application.Visible = -1
                except Exception:
                    pass
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
        error = (
            "PowerPoint COM activation failed before the desktop fallback. "
            f"Direct Dispatch: {dispatch_error}; "
            f"launcher: {launch_error or launch_method}; "
            f"process_detected={process_detected}."
        )
        return PowerPointBootstrapResult(
            ok=False,
            launched=launched,
            launch_method=launch_method,
            executable=executable,
            process_detected=process_detected,
            attempts=attempts,
            duration_ms=round((time.monotonic() - started_at) * 1000),
            error=error,
        )
    finally:
        application = None
        pythoncom.CoUninitialize()
