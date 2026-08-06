from __future__ import annotations

import csv
import os
import subprocess
from io import StringIO
from pathlib import Path

from app import windows_window_placement


def _timeout_seconds() -> float:
    try:
        value = float(os.getenv("SMART_OFFICE_TASKLIST_TIMEOUT_SECONDS", "1.5"))
    except ValueError:
        value = 1.5
    return max(0.2, min(5.0, value))


def _normalise_process_name(value: str) -> str:
    return Path(str(value).strip().strip('"')).name.casefold()


def _tasklist_processes_with_deadline() -> dict[int, str]:
    if os.name != "nt":
        return {}
    try:
        completed = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_timeout_seconds(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return {}

    result: dict[int, str] = {}
    for row in csv.reader(StringIO(completed.stdout or "")):
        if len(row) < 2:
            continue
        try:
            pid = int(str(row[1]).replace(",", "").strip())
        except ValueError:
            continue
        result[pid] = str(row[0]).strip()
    return result


# matching_windows resolves this global at call time, so replacing it here removes
# the only unbounded subprocess wait without changing the placement API contract.
windows_window_placement._tasklist_processes = _tasklist_processes_with_deadline
windows_window_placement._normalise_process_name = _normalise_process_name
