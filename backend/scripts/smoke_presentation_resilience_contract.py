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
        "MEGA_SMART_Exhibition_Narration_and_QA_EN.docx",
        "MEGA_SMART_展览讲解稿与技术答疑知识库.docx",
        "_SCRIPT_CACHE",
        "asyncio.to_thread(_load_script, language)",
        "asyncio.to_thread(_load_script, req.language)",
        "SMART_OFFICE_PRESENTATION_QA_TIMEOUT_SECONDS",
        "_SLIDE_HEADING_EN",
        "_NARRATION_MARKER_EN",
        "_TECH_MARKER_EN",
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
        "any explicit PowerPoint/PPT",
        "const START_PRESENTATION = /(?:\\bppt\\b|\\bpower\\s*point\\b|\\bpowerpoint\\b|幻灯片|演示文稿)/i",
    )

    language_patch = source(
        "ui/smart-office-ui/src/voice/presentationLanguageFetchPatch.ts"
    )
    require(
        language_patch,
        "visitLanguagePreference.current()",
        "/api/presentation/session/script",
        "url.searchParams.set('language'",
    )

    drawer = source("ui/smart-office-ui/src/virtual-host/OperatorDrawer.tsx")
    require(
        drawer,
        "runtimeSystemPaused",
        "runtimeRestoreProximity",
        "toggleSystemPause",
        "proximity.setEnabled(false)",
        "controller.stopSpeaking()",
        "smartoffice:system-pause-changed",
        "恢复系统与摄像头",
        "Resume system and camera",
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
        "presentationLanguageFetchPatch",
        "stale-vad-transcription-skipped",
        "latestStoppedItemId",
    )

    speech_runtime = source(
        "ui/smart-office-ui/src/voice/realtimeSpeechRuntime.ts"
    )
    require(
        speech_runtime,
        "VITE_REALTIME_SEPARATE_SPEECH_SESSION",
        "new PersistentRealtimeAgent()",
        "originalPrimaryStopOutput",
        "realtimeSpeechAgent.shutdown()",
    )

    expressive = source(
        "ui/smart-office-ui/src/voice/expressiveRealtimeSpeech.ts"
    )
    require(
        expressive,
        "realtimeSpeechAgent",
        "Continuous ASR remains on the primary session",
    )

    print("presentation resilience contract: PASS")


if __name__ == "__main__":
    main()
