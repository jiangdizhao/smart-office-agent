from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        compile(text, str(path), "exec")
    return text


def require(text: str, *needles: str) -> None:
    for needle in needles:
        assert needle in text, f"missing resilience contract token: {needle}"


def main() -> None:
    worker = source("backend/app/presentation_worker_supervisor.py")
    require(
        worker,
        'mp.get_context("spawn")',
        "PresentationWorkerTimeoutError",
        "process.terminate()",
        'SMART_OFFICE_POWERPOINT_KILL_ON_WORKER_TIMEOUT',
        'SMART_OFFICE_WORKER_CHILD',
        "taskkill",
    )

    api = source("backend/app/presentation_api.py")
    require(
        api,
        '@router.post("/guided/start"',
        '@router.post("/guided/finish"',
        "asyncio.to_thread",
        "worker_process_isolation",
    )

    session = source("backend/app/presentation_session_api.py")
    require(
        session,
        "_SCRIPT_CACHE_KEY",
        "asyncio.to_thread(_load_script)",
        "asyncio.wait_for",
        "SMART_OFFICE_PRESENTATION_QA_TIMEOUT_SECONDS",
    )

    current_slide = source("backend/app/current_slide_insight.py")
    require(
        current_slide,
        "presentation_worker.status()",
        "worker_process_isolation",
    )

    task_watchdog = source("backend/app/task_watchdog.py")
    require(
        task_watchdog,
        "SMART_OFFICE_TASK_RUNTIME_TIMEOUT_SECONDS",
        'task.status in {"created", "planning", "running"}',
        "task_runtime_stalled",
    )

    controller = source(
        "ui/smart-office-ui/src/voice/useGuidedPresentationController.ts"
    )
    require(
        controller,
        "AbortController",
        "TRANSIENT_STATE_BUDGET_MS",
        "operationRef",
        "'/api/presentation/guided/start'",
        "'/api/presentation/guided/finish'",
        "setSessionState('inactive', 1)",
        "sessionWasActive",
    )

    preemptive = source(
        "ui/smart-office-ui/src/voice/preemptiveTurnCoordinator.ts"
    )
    require(
        preemptive,
        "'/api/presentation'",
        "requestDeadlineMs",
        "vad-liveness-timeout",
        "realtimeLatestUtterancePatch",
    )

    latest = source(
        "ui/smart-office-ui/src/voice/realtimeLatestUtterancePatch.ts"
    )
    require(
        latest,
        "stale-vad-transcription-skipped",
        "latestStoppedItemId",
    )

    print("presentation resilience contract: PASS")


if __name__ == "__main__":
    main()
