from __future__ import annotations

import multiprocessing as mp
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

from app import windows_window_placement_tasklist_patch as _tasklist_timeout_patch  # noqa: F401
from app.models import ToolResult, VerificationResult


class PresentationWorkerError(RuntimeError):
    """Base error for the killable PowerPoint worker."""


class PresentationWorkerTimeoutError(PresentationWorkerError, TimeoutError):
    pass


class PresentationWorkerUnavailableError(PresentationWorkerError):
    pass


@dataclass(frozen=True)
class PresentationWorkerStep:
    name: str
    tool_result: ToolResult
    verification_result: VerificationResult
    status: ToolResult


@dataclass(frozen=True)
class PresentationWorkerExecution:
    operation_id: str
    steps: tuple[PresentationWorkerStep, ...]
    duration_ms: int
    worker_process_isolation: bool

    @property
    def final(self) -> PresentationWorkerStep:
        if not self.steps:
            raise PresentationWorkerUnavailableError(
                "The PowerPoint worker returned no execution step."
            )
        return self.steps[-1]

    @property
    def ok(self) -> bool:
        return bool(
            self.steps
            and all(
                step.tool_result.ok and step.verification_result.ok
                for step in self.steps
            )
        )


def _worker_main(command_queue: Any, response_queue: Any) -> None:
    # Import only inside the child so the FastAPI process never holds PowerPoint
    # COM objects. A wedged COM call can therefore be terminated with this worker.
    from app.presentation_actions import execute_presentation_tool_call

    while True:
        request = command_queue.get()
        if request is None:
            return
        operation_id = str(request.get("operation_id") or "")
        commands = list(request.get("commands") or [])
        started_at = time.monotonic()
        steps: list[dict[str, Any]] = []
        try:
            for command in commands:
                name = str(command.get("name") or "")
                arguments = dict(command.get("arguments") or {})
                tool_result, verification, status = execute_presentation_tool_call(
                    name,
                    arguments,
                )
                steps.append(
                    {
                        "name": name,
                        "tool_result": tool_result.model_dump(mode="json"),
                        "verification_result": verification.model_dump(mode="json"),
                        "status": status.model_dump(mode="json"),
                    }
                )
                if not tool_result.ok or not verification.ok:
                    break
            response_queue.put(
                {
                    "operation_id": operation_id,
                    "steps": steps,
                    "duration_ms": round((time.monotonic() - started_at) * 1000),
                }
            )
        except BaseException as exc:  # child must report any failure to parent
            response_queue.put(
                {
                    "operation_id": operation_id,
                    "steps": steps,
                    "duration_ms": round((time.monotonic() - started_at) * 1000),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )


def _truthy_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


class PresentationWorkerSupervisor:
    """Serialize PowerPoint actions through a killable child process.

    Python threads cannot safely stop a blocked COM call. On Windows this supervisor
    places all PowerPoint automation in one spawned process. When a command misses
    its deadline, the process is terminated, optional dedicated-demo PowerPoint
    cleanup is performed, and the next request receives a fresh worker.

    Non-Windows contract environments execute in-process by default so existing
    monkeypatch-based tests remain deterministic. The behavior can be overridden
    with SMART_OFFICE_POWERPOINT_WORKER_ENABLED.
    """

    def __init__(self) -> None:
        self._context = mp.get_context("spawn")
        self._lock = threading.RLock()
        self._process: mp.Process | None = None
        self._command_queue: Any | None = None
        self._response_queue: Any | None = None

    def worker_enabled(self) -> bool:
        return _truthy_env("SMART_OFFICE_POWERPOINT_WORKER_ENABLED", os.name == "nt")

    def timeout_for(self, name: str) -> float:
        defaults = {
            "presentation_get_status": 4.0,
            "presentation_next_slide": 4.0,
            "presentation_previous_slide": 4.0,
            "presentation_go_to_slide": 4.0,
            "presentation_end_slideshow": 8.0,
            "presentation_start_slideshow": 10.0,
            "presentation_open_configured": 15.0,
            "presentation_close": 10.0,
        }
        fallback = defaults.get(name, 10.0)
        suffix = name.removeprefix("presentation_").upper()
        return _float_env(
            f"SMART_OFFICE_POWERPOINT_{suffix}_TIMEOUT_SECONDS",
            fallback,
            1.0,
            60.0,
        )

    def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> PresentationWorkerExecution:
        return self.execute_sequence(
            [(name, dict(arguments or {}))],
            timeout_seconds=timeout_seconds or self.timeout_for(name),
        )

    def execute_sequence(
        self,
        commands: Iterable[tuple[str, dict[str, Any]]],
        *,
        timeout_seconds: float,
    ) -> PresentationWorkerExecution:
        normalized = [
            {"name": str(name), "arguments": dict(arguments or {})}
            for name, arguments in commands
        ]
        if not normalized:
            raise ValueError("At least one PowerPoint command is required.")

        operation_id = str(uuid4())
        if not self.worker_enabled():
            return self._execute_direct(normalized, operation_id)

        with self._lock:
            self._ensure_worker()
            assert self._command_queue is not None
            assert self._response_queue is not None
            assert self._process is not None
            self._command_queue.put(
                {
                    "operation_id": operation_id,
                    "commands": normalized,
                }
            )
            deadline = time.monotonic() + max(0.5, timeout_seconds)
            while True:
                if not self._process.is_alive():
                    self._restart_worker(kill_powerpoint=False)
                    raise PresentationWorkerUnavailableError(
                        "The PowerPoint worker exited before returning a result."
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._restart_worker(kill_powerpoint=True)
                    raise PresentationWorkerTimeoutError(
                        f"PowerPoint operation timed out after {timeout_seconds:.1f} seconds."
                    )
                try:
                    payload = self._response_queue.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue
                if str(payload.get("operation_id") or "") != operation_id:
                    continue
                if payload.get("error"):
                    error_type = str(payload.get("error_type") or "PowerPointWorkerError")
                    detail = str(payload.get("error") or "unknown worker failure")
                    self._restart_worker(kill_powerpoint=False)
                    raise PresentationWorkerUnavailableError(
                        f"{error_type}: {detail}"
                    )
                steps = tuple(
                    PresentationWorkerStep(
                        name=str(step.get("name") or ""),
                        tool_result=ToolResult.model_validate(step.get("tool_result")),
                        verification_result=VerificationResult.model_validate(
                            step.get("verification_result")
                        ),
                        status=ToolResult.model_validate(step.get("status")),
                    )
                    for step in list(payload.get("steps") or [])
                )
                return PresentationWorkerExecution(
                    operation_id=operation_id,
                    steps=steps,
                    duration_ms=int(payload.get("duration_ms") or 0),
                    worker_process_isolation=True,
                )

    def _execute_direct(
        self,
        normalized: list[dict[str, Any]],
        operation_id: str,
    ) -> PresentationWorkerExecution:
        from app.presentation_actions import execute_presentation_tool_call

        started_at = time.monotonic()
        steps: list[PresentationWorkerStep] = []
        for command in normalized:
            name = str(command.get("name") or "")
            tool_result, verification, status = execute_presentation_tool_call(
                name,
                dict(command.get("arguments") or {}),
            )
            steps.append(
                PresentationWorkerStep(
                    name=name,
                    tool_result=tool_result,
                    verification_result=verification,
                    status=status,
                )
            )
            if not tool_result.ok or not verification.ok:
                break
        return PresentationWorkerExecution(
            operation_id=operation_id,
            steps=tuple(steps),
            duration_ms=round((time.monotonic() - started_at) * 1000),
            worker_process_isolation=False,
        )

    def execute_tool_result(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        execution = self.execute(
            name,
            arguments,
            timeout_seconds=timeout_seconds,
        )
        final = execution.final
        return final.tool_result.model_copy(
            deep=True,
            update={
                "data": {
                    **final.tool_result.data,
                    "worker_operation_id": execution.operation_id,
                    "worker_duration_ms": execution.duration_ms,
                    "worker_process_isolation": execution.worker_process_isolation,
                    "verification": final.verification_result.model_dump(mode="json"),
                    "presentation_status": final.status.model_dump(mode="json"),
                }
            },
        )

    def status(self) -> ToolResult:
        return self.execute("presentation_get_status").final.status

    def shutdown(self) -> None:
        with self._lock:
            self._stop_worker()

    def _ensure_worker(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._stop_worker()
        self._command_queue = self._context.Queue()
        self._response_queue = self._context.Queue()
        self._process = self._context.Process(
            target=_worker_main,
            args=(self._command_queue, self._response_queue),
            name="smart-office-powerpoint-worker",
            daemon=True,
        )
        previous_worker_marker = os.environ.get("SMART_OFFICE_WORKER_CHILD")
        os.environ["SMART_OFFICE_WORKER_CHILD"] = "1"
        try:
            self._process.start()
        finally:
            if previous_worker_marker is None:
                os.environ.pop("SMART_OFFICE_WORKER_CHILD", None)
            else:
                os.environ["SMART_OFFICE_WORKER_CHILD"] = previous_worker_marker

    def _stop_worker(self) -> None:
        process = self._process
        command_queue = self._command_queue
        if process is not None and process.is_alive():
            try:
                command_queue.put_nowait(None)
            except Exception:
                pass
            process.join(timeout=0.5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.5)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1.0)
        for item in (self._command_queue, self._response_queue):
            try:
                item.close()
            except Exception:
                pass
        self._process = None
        self._command_queue = None
        self._response_queue = None

    def _restart_worker(self, *, kill_powerpoint: bool) -> None:
        self._stop_worker()
        if kill_powerpoint and _truthy_env(
            "SMART_OFFICE_POWERPOINT_KILL_ON_WORKER_TIMEOUT",
            True,
        ):
            self._kill_powerpoint_processes()
        self._ensure_worker()

    @staticmethod
    def _kill_powerpoint_processes() -> None:
        if os.name != "nt":
            return
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/IM", "POWERPNT.EXE"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass


presentation_worker = PresentationWorkerSupervisor()
