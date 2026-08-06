from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.current_slide_insight import current_slide_insight
from app.models import ToolResult, VerificationResult
from app.presentation_config import presentation_config
from app.presentation_session_api import router as presentation_session_router
from app.presentation_worker_supervisor import (
    PresentationWorkerError,
    PresentationWorkerExecution,
    PresentationWorkerTimeoutError,
    presentation_worker,
)
from app.whole_presentation_insight import whole_presentation_insight


router = APIRouter(prefix="/api/presentation", tags=["presentation-gate1"])
router.include_router(presentation_session_router)


class GoToSlideRequest(BaseModel):
    slide_number: int = Field(..., ge=1, le=100_000)


class ClosePresentationRequest(BaseModel):
    confirmed: bool = False


class CurrentSlideInsightRequest(BaseModel):
    mode: Literal["summary", "explanation", "whole_summary"] = "summary"
    language: Literal["zh", "en"] = "zh"


class PresentationStatusResponse(BaseModel):
    ok: bool
    phase: str = "m3a_fusion_phase_3_gate_1"
    config: dict
    status: ToolResult
    worker_process_isolation: bool


class PresentationActionResponse(BaseModel):
    ok: bool
    phase: str = "m3a_fusion_phase_3_gate_1"
    tool_result: ToolResult
    verification_result: VerificationResult
    status: ToolResult
    operation_id: str
    worker_duration_ms: int
    worker_process_isolation: bool
    step_count: int = 1


class CurrentSlideInsightResponse(BaseModel):
    ok: bool
    phase: str = "presentation_insight_v2"
    mode: Literal["summary", "explanation", "whole_summary"]
    spoken_text: str
    result: ToolResult


def _http_error(exc: PresentationWorkerError) -> HTTPException:
    status_code = 504 if isinstance(exc, PresentationWorkerTimeoutError) else 503
    return HTTPException(
        status_code=status_code,
        detail={
            "message": str(exc),
            "worker_process_isolation": presentation_worker.worker_enabled(),
            "recoverable": True,
        },
    )


def _response_from_execution(
    execution: PresentationWorkerExecution,
) -> PresentationActionResponse:
    final = execution.final
    return PresentationActionResponse(
        ok=execution.ok,
        tool_result=final.tool_result,
        verification_result=final.verification_result,
        status=final.status,
        operation_id=execution.operation_id,
        worker_duration_ms=execution.duration_ms,
        worker_process_isolation=execution.worker_process_isolation,
        step_count=len(execution.steps),
    )


async def _execute(
    name: str,
    arguments: dict | None = None,
    *,
    timeout_seconds: float | None = None,
) -> PresentationActionResponse:
    try:
        execution = await asyncio.to_thread(
            presentation_worker.execute,
            name,
            arguments or {},
            timeout_seconds=timeout_seconds,
        )
    except PresentationWorkerError as exc:
        raise _http_error(exc) from exc
    return _response_from_execution(execution)


async def _execute_sequence(
    commands: list[tuple[str, dict]],
    *,
    timeout_seconds: float,
) -> PresentationActionResponse:
    try:
        execution = await asyncio.to_thread(
            presentation_worker.execute_sequence,
            commands,
            timeout_seconds=timeout_seconds,
        )
    except PresentationWorkerError as exc:
        raise _http_error(exc) from exc
    return _response_from_execution(execution)


@router.get("/status", response_model=PresentationStatusResponse)
async def presentation_status() -> PresentationStatusResponse:
    try:
        status = await asyncio.to_thread(presentation_worker.status)
    except PresentationWorkerError as exc:
        raise _http_error(exc) from exc
    return PresentationStatusResponse(
        ok=status.ok,
        config=presentation_config.public_dict(),
        status=status,
        worker_process_isolation=presentation_worker.worker_enabled(),
    )


@router.post("/current-slide/insight", response_model=CurrentSlideInsightResponse)
async def presentation_current_slide_insight(
    req: CurrentSlideInsightRequest,
) -> CurrentSlideInsightResponse:
    result = (
        await whole_presentation_insight(language=req.language)
        if req.mode == "whole_summary"
        else await asyncio.to_thread(
            current_slide_insight,
            mode=req.mode,
            language=req.language,
        )
    )
    return CurrentSlideInsightResponse(
        ok=result.ok,
        mode=req.mode,
        spoken_text=result.message,
        result=result,
    )


@router.post("/guided/start", response_model=PresentationActionResponse)
async def presentation_guided_start() -> PresentationActionResponse:
    # One deterministic start path only. presentation_start_slideshow already opens
    # the configured deck when it is not open and reuses the existing slide-show
    # window when it is active. Calling presentation_open_configured first duplicated
    # window-placement/status work and could overrun the worker deadline, which then
    # killed POWERPNT.EXE and allowed a repeated transcript to restart the cycle.
    # Start/reuse the show, force slide 1, and return. Never close or restart PPT here.
    return await _execute_sequence(
        [
            ("presentation_start_slideshow", {}),
            ("presentation_go_to_slide", {"slide_number": 1}),
        ],
        timeout_seconds=24.0,
    )


@router.post("/guided/finish", response_model=PresentationActionResponse)
async def presentation_guided_finish() -> PresentationActionResponse:
    return await _execute_sequence(
        [
            ("presentation_go_to_slide", {"slide_number": 1}),
            ("presentation_end_slideshow", {}),
        ],
        timeout_seconds=14.0,
    )


@router.post("/open", response_model=PresentationActionResponse)
async def presentation_open() -> PresentationActionResponse:
    return await _execute("presentation_open_configured")


@router.post("/slideshow/start", response_model=PresentationActionResponse)
async def presentation_start_slideshow() -> PresentationActionResponse:
    return await _execute("presentation_start_slideshow")


@router.post("/slideshow/next", response_model=PresentationActionResponse)
async def presentation_next_slide() -> PresentationActionResponse:
    return await _execute("presentation_next_slide")


@router.post("/slideshow/previous", response_model=PresentationActionResponse)
async def presentation_previous_slide() -> PresentationActionResponse:
    return await _execute("presentation_previous_slide")


@router.post("/slideshow/goto", response_model=PresentationActionResponse)
async def presentation_go_to_slide(req: GoToSlideRequest) -> PresentationActionResponse:
    return await _execute(
        "presentation_go_to_slide",
        {"slide_number": req.slide_number},
    )


@router.post("/slideshow/end", response_model=PresentationActionResponse)
async def presentation_end_slideshow() -> PresentationActionResponse:
    return await _execute("presentation_end_slideshow")


@router.post("/close", response_model=PresentationActionResponse)
async def presentation_close(req: ClosePresentationRequest) -> PresentationActionResponse:
    if not req.confirmed:
        raise HTTPException(
            status_code=409,
            detail="Closing PowerPoint without saving changes requires confirmed=true.",
        )
    return await _execute("presentation_close")
