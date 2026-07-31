from __future__ import annotations

import atexit
import multiprocessing
import os
import queue
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.models import ToolResult, VerificationResult

_SPAWN_ENV_LOCK = threading.Lock()
_WORKER_CHILD_ENV = "SMART_OFFICE_WORKER_CHILD"
_STATUS_OPERATION = "__office_status__"


def _timeout_seconds() -> float:
    try:
        return max(3.0, float(os.getenv("SMART_OFFICE_TOOL_PROCESS_TIMEOUT_SECONDS", "15")))
    except ValueError:
        return 15.0


def _status_payload() -> tuple[ToolResult, VerificationResult, ToolResult]:
    from app.office_actions import get_office_status

    status = get_office_status()
    verification = VerificationResult(
        ok=status.ok,
        message=(
            "Office status was inspected in the isolated worker."
            if status.ok
            else f"Office status inspection failed: {status.message}"
        ),
        checked_at=datetime.now(UTC),
        raw={"worker_process": True, "status_only": True},
    )
    return status, verification, status


def _worker_loop(requests: Any, responses: Any) -> None:
    """Serial Office/COM worker. The parent may terminate this process safely."""

    while True:
        job = requests.get()
        if job is None:
            return
        request_id = str(job.get("request_id") or "")
        tool_name = str(job.get("tool_name") or "")
        args = dict(job.get("args") or {})
        try:
            if tool_name == _STATUS_OPERATION:
                result, verification, status = _status_payload()
            else:
                from app.office_actions import execute_office_tool_call_direct

                result, verification, status = execute_office_tool_call_direct(tool_name, args)
            payload = {
                "request_id": request_id,
                "ok": True,
                "result": result.model_dump(mode="json"),
                "verification": verification.model_dump(mode="json"),
                "status": status.model_dump(mode="json"),
            }
        except BaseException as exc:
            payload = {
                "request_id": request_id,
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        try:
            responses.put(payload)
        except BaseException:
            return


def _failure(
    tool_name: str,
    message: str,
    *,
    timed_out: bool,
    duration_ms: int,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    public_name = "office_get_status" if tool_name == _STATUS_OPERATION else tool_name
    result = ToolResult(
        tool_name=public_name,
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


def _start_marked_worker(process: multiprocessing.Process) -> None:
    """Spawn a child that skips Backend-only daemon services."""

    with _SPAWN_ENV_LOCK:
        previous = os.environ.get(_WORKER_CHILD_ENV)
        os.environ[_WORKER_CHILD_ENV] = "1"
        try:
            process.start()
        finally:
            if previous is None:
                os.environ.pop(_WORKER_CHILD_ENV, None)
            else:
                os.environ[_WORKER_CHILD_ENV] = previous


class OfficeWorkerBroker:
    """One reusable Office process with hard restart on timeout or crash."""

    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.RLock()
        self._process: multiprocessing.Process | None = None
        self._requests: Any | None = None
        self._responses: Any | None = None
        self._generation = 0

    def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> tuple[ToolResult, VerificationResult, ToolResult]:
        timeout = _timeout_seconds() if timeout_seconds is None else max(1.0, timeout_seconds)
        started = time.monotonic()
        request_id = f"office_{uuid4().hex}"
        with self._lock:
            self._ensure_worker_locked()
            process = self._process
            requests = self._requests
            responses = self._responses
            if process is None or requests is None or responses is None:
                return _failure(
                    tool_name,
                    "Office worker process could not be started.",
                    timed_out=False,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            try:
                requests.put(
                    {
                        "request_id": request_id,
                        "tool_name": tool_name,
                        "args": dict(args),
                        "generation": self._generation,
                    },
                    timeout=1.0,
                )
            except Exception as exc:
                self._terminate_locked()
                return _failure(
                    tool_name,
                    f"Could not submit Office worker request: {exc}",
                    timed_out=False,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )

            deadline = time.monotonic() + timeout
            payload: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                if not process.is_alive():
                    break
                remaining = max(0.01, min(0.25, deadline - time.monotonic()))
                try:
                    candidate = responses.get(timeout=remaining)
                except queue.Empty:
                    continue
                if str(candidate.get("request_id") or "") == request_id:
                    payload = candidate
                    break
                # Calls are serialized by the broker lock. A mismatched response
                # can only be stale data from a worker generation being replaced.

            duration_ms = round((time.monotonic() - started) * 1000)
            if payload is None:
                was_alive = process.is_alive()
                self._terminate_locked()
                message = (
                    f"Office worker process timed out after {timeout:.1f} seconds and was terminated."
                    if was_alive
                    else f"Office worker process exited unexpectedly with code {process.exitcode}."
                )
                return _failure(
                    tool_name,
                    message,
                    timed_out=was_alive,
                    duration_ms=duration_ms,
                )

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
                    "worker_pid": process.pid,
                    "worker_generation": self._generation,
                }
            )
            return result, verification, status

    def inspect_status(
        self,
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        result, _verification, status = self.execute(
            _STATUS_OPERATION,
            {},
            timeout_seconds=timeout_seconds,
        )
        return status if status.ok else result

    def shutdown(self) -> None:
        with self._lock:
            if self._requests is not None and self._process is not None and self._process.is_alive():
                try:
                    self._requests.put_nowait(None)
                    self._process.join(1.0)
                except Exception:
                    pass
            self._terminate_locked()

    def _ensure_worker_locked(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._terminate_locked()
        self._generation += 1
        requests = self._context.Queue(maxsize=8)
        responses = self._context.Queue(maxsize=8)
        process = self._context.Process(
            target=_worker_loop,
            args=(requests, responses),
            name=f"smart-office-worker-{self._generation}",
            daemon=False,
        )
        _start_marked_worker(process)
        self._requests = requests
        self._responses = responses
        self._process = process

    def _terminate_locked(self) -> None:
        process = self._process
        self._process = None
        requests = self._requests
        responses = self._responses
        self._requests = None
        self._responses = None
        if process is not None and process.is_alive():
            process.terminate()
            process.join(2.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(1.0)
        for channel in (requests, responses):
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass


_office_worker_broker = OfficeWorkerBroker()
atexit.register(_office_worker_broker.shutdown)


def execute_office_tool_isolated(
    tool_name: str,
    args: dict[str, Any],
    timeout_seconds: float | None = None,
) -> tuple[ToolResult, VerificationResult, ToolResult]:
    return _office_worker_broker.execute(tool_name, args, timeout_seconds)


def inspect_office_status_isolated(timeout_seconds: float | None = None) -> ToolResult:
    return _office_worker_broker.inspect_status(timeout_seconds)
