import os
import shutil
import subprocess
from pathlib import Path

from app.models import ToolResult


def _start_windows_command(
    *,
    tool_name: str,
    command: list[str],
    expected_process_names: list[str],
    expected_window_keywords: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> ToolResult:
    try:
        process = subprocess.Popen(command, shell=False)
        return ToolResult(
            tool_name=tool_name,
            ok=True,
            message="Command started.",
            launched_pid=process.pid,
            expected_process_names=expected_process_names,
            expected_window_keywords=expected_window_keywords or [],
            artifacts=artifacts or [],
            raw={"command": command},
        )
    except Exception as exc:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Failed to start command: {exc}",
            expected_process_names=expected_process_names,
            expected_window_keywords=expected_window_keywords or [],
            artifacts=artifacts or [],
            raw={"command": command, "error": str(exc)},
        )


def _start_executable(
    *,
    tool_name: str,
    path: Path,
    expected_process_names: list[str],
    expected_window_keywords: list[str] | None = None,
) -> ToolResult:
    try:
        process = subprocess.Popen([str(path)], shell=False)
        return ToolResult(
            tool_name=tool_name,
            ok=True,
            message=f"Started {path} with pid {process.pid}.",
            launched_pid=process.pid,
            expected_process_names=expected_process_names,
            expected_window_keywords=expected_window_keywords or [],
            raw={"executable": str(path)},
        )
    except Exception as exc:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Failed to start {path}: {exc}",
            expected_process_names=expected_process_names,
            expected_window_keywords=expected_window_keywords or [],
            raw={"executable": str(path), "error": str(exc)},
        )


def _find_zoom_executable() -> Path | None:
    explicit_candidates = []
    configured_zoom_path = os.environ.get("SMART_OFFICE_ZOOM_PATH")
    if configured_zoom_path:
        explicit_candidates.append(Path(configured_zoom_path))

    env_candidates = []
    for env_name in ["APPDATA", "USERPROFILE"]:
        env_value = os.environ.get(env_name)
        if env_value:
            env_candidates.append(Path(env_value))

    for base in env_candidates:
        if base.name.lower() == "roaming":
            explicit_candidates.append(base / "Zoom" / "bin_00" / "Zoom.exe")
            explicit_candidates.extend(sorted((base / "Zoom").glob("bin*/Zoom.exe")))
        else:
            roaming = base / "AppData" / "Roaming"
            explicit_candidates.append(roaming / "Zoom" / "bin_00" / "Zoom.exe")
            explicit_candidates.extend(sorted((roaming / "Zoom").glob("bin*/Zoom.exe")))

    for candidate in explicit_candidates:
        if candidate.exists():
            return candidate

    path_match = shutil.which("Zoom.exe") or shutil.which("zoom")
    if path_match:
        return Path(path_match)

    return None


def open_edge(url: str = "http://localhost:5173") -> ToolResult:
    return _start_windows_command(
        tool_name="open_edge",
        command=["cmd", "/c", "start", "", "msedge", url],
        expected_process_names=["msedge.exe"],
        expected_window_keywords=["Edge", url],
    )


def open_zoom() -> ToolResult:
    zoom_path = _find_zoom_executable()
    if zoom_path is None:
        return ToolResult(
            tool_name="open_zoom",
            ok=False,
            message="Zoom executable was not found. Checked SMART_OFFICE_ZOOM_PATH, APPDATA Zoom paths, USERPROFILE Zoom paths, and PATH.",
            expected_process_names=["Zoom.exe"],
        )

    return _start_executable(
        tool_name="open_zoom",
        path=zoom_path,
        expected_process_names=["Zoom.exe"],
        expected_window_keywords=["Zoom"],
    )


def open_word() -> ToolResult:
    return _start_windows_command(
        tool_name="open_word",
        command=["cmd", "/c", "start", "", "winword"],
        expected_process_names=["WINWORD.EXE"],
        expected_window_keywords=["Word"],
    )


def open_excel() -> ToolResult:
    return _start_windows_command(
        tool_name="open_excel",
        command=["cmd", "/c", "start", "", "excel"],
        expected_process_names=["EXCEL.EXE"],
        expected_window_keywords=["Excel"],
    )


def open_powerpoint() -> ToolResult:
    return _start_windows_command(
        tool_name="open_powerpoint",
        command=["cmd", "/c", "start", "", "powerpnt"],
        expected_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
    )


def open_onenote() -> ToolResult:
    # OneNote command name may differ depending on installation.
    # This is enough for first demo skeleton.
    return _start_windows_command(
        tool_name="open_onenote",
        command=["cmd", "/c", "start", "", "onenote"],
        expected_process_names=["ONENOTE.EXE"],
        expected_window_keywords=["OneNote"],
    )


def open_sample_document() -> ToolResult:
    sample_dir = Path("C:/smart-office-agent/data")
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_file = sample_dir / "meeting_agenda_demo.txt"

    if not sample_file.exists():
        sample_file.write_text(
            "Smart Office Agent Demo Meeting Agenda\n\n"
            "1. Review project status\n"
            "2. Discuss customer requirements\n"
            "3. Prepare follow-up actions\n",
            encoding="utf-8",
        )

    return _start_windows_command(
        tool_name="open_sample_document",
        command=["notepad.exe", str(sample_file)],
        expected_process_names=["notepad.exe"],
        expected_window_keywords=[sample_file.name],
        artifacts=[str(sample_file)],
    )
