from __future__ import annotations

import multiprocessing
import os
import queue
import time
from datetime import UTC, datetime
from typing import Any

from app.models import ToolResult, VerificationResult


def _timeout_seconds() -> float:
    try:
        return max(3.0, float(os.getenv("SMART_OFFICE_TOOL_PROCESS_TIMEOUT_SECONDS", "15")))
    except ValueError:
        return 15.0


def _worker(
    output: Any,
    tool_name: str,
    args: dict[str, Any],
) -> None:
    try:
        from app.office_actions import execute_office_tool_call_direct

        result, verification, status = execute_office_tool_call_direct(tool_name, args)
        output.put(
            {
                "ok": True,
                "result": result.model_dump(mode="json"),
                "verification": verification.model_dump(mode="json"),
                "status": status.model_dump(mode="json"),
            }
        )
    except BaseException as exc:
        output.put(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )


def _failure(
    tool_name: str,
    message: str,
    *,
    timed_out: bool,
    duration_ms: int,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    result = ToolResult(
        tool_name=tool_name,
        ok=False,
        message=message,
        data={
            "execution_mode": "isolated_worker_process",
            "timed_out": timed_out,
            "duration_ms": duration_ms,
        },
        raw={"timed_out": timed_out},
    )
    verification = VerificationResult(
        ok=False,
        message=message,
        checked_at=datetime.now(UTC),
        raw={"worker_process": True, "timed_out": timed_out},
    )
    status = ToolResult(
        tool_name="office_get_status",
        ok=False,
        message="Office status is unavailable because the isolated worker did not complete.",
        data={"worker_process": True, "timed_out": timed_out},
    )
    return result, verification, status


def execute_office_tool_isolated(
    tool_name: str,
    args: dict[str, Any],
    timeout_seconds: float | None = None,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    """Execute one Office/COM action in a process that can be hard-terminated."""

    timeout = _timeout_seconds() if timeout_seconds is None else max(1.0, timeout_seconds)
    started = time.monotonic()
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_worker,
        args=(output, tool_name, dict(args)),
        name=f"office-tool-{tool_name}",
        daemon=False,
    )
    process.start()
    process.join(timeout)

    if process.is_alive():
        process.terminate()
        process.join(2.0)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(1.0)
        output.close()
        duration_ms = round((time.monotonic() - started) * 1000)
        return _failure(
            tool_name,
            f"Office worker process timed out after {timeout:.1f} seconds and was terminated.",
            timed_out=True,
            duration_ms=duration_ms,
        )

    try:
        payload = output.get(timeout=1.0)
    except queue.Empty:
        payload = {
            "ok": False,
            "error_type": "WorkerExited",
            "error": f"worker exit code {process.exitcode}",
        }
    finally:
        output.close()

    duration_ms = round((time.monotonic() - started) * 1000)
    if not payload.get("ok"):
        return _failure(
            tool_name,
            "Office worker failed: "
            f"{payload.get('error_type', 'Error')}: {payload.get('error', 'unknown error')}",
            timed_out=False,
            duration_ms=duration_ms,
        )

    result = ToolResult.model_validate(payload["result"])
    verification = VerificationResult.model_validate(payload["verification"])
    status = ToolResult.model_validate(payload["status"])
    result.data.update(
        {
            "execution_mode": "isolated_worker_process",
            "worker_duration_ms": duration_ms,
            "worker_exit_code": process.exitcode,
        }
    )
    return result, verification, status
