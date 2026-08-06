from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.current_slide_insight import current_slide_insight
from app.models import ToolResult, VerificationResult
from app.presentation_actions import execute_presentation_tool_call
from app.presentation_config import presentation_config
from app.tools.presentation_controller import get_presentation_status


router = APIRouter(prefix="/api/presentation", tags=["presentation-gate1"])


class GoToSlideRequest(BaseModel):
    slide_number: int = Field(..., ge=1, le=100_000)


class ClosePresentationRequest(BaseModel):
    confirmed: bool = False


class CurrentSlideInsightRequest(BaseModel):
    mode: Literal["summary", "explanation"] = "summary"
    language: Literal["zh", "en"] = "zh"


class PresentationStatusResponse(BaseModel):
    ok: bool
    phase: str = "m3a_fusion_phase_3_gate_1"
    config: dict
    status: ToolResult


class PresentationActionResponse(BaseModel):
    ok: bool
    phase: str = "m3a_fusion_phase_3_gate_1"
    tool_result: ToolResult
    verification_result: VerificationResult


class CurrentSlideInsightResponse(BaseModel):
    ok: bool
    phase: str = "current_slide_insight_v1"
    mode: Literal["summary", "explanation"]
    spoken_text: str
    result: ToolResult


def _execute(name: str, arguments: dict | None = None) -> PresentationActionResponse:
    tool_result, verification, _status = execute_presentation_tool_call(
        name,
        arguments or {},
    )
    return PresentationActionResponse(
        ok=tool_result.ok and verification.ok,
        tool_result=tool_result,
        verification_result=verification,
    )


@router.get("/status", response_model=PresentationStatusResponse)
def presentation_status() -> PresentationStatusResponse:
    status = get_presentation_status()
    return PresentationStatusResponse(
        ok=status.ok,
        config=presentation_config.public_dict(),
        status=status,
    )


@router.post("/current-slide/insight", response_model=CurrentSlideInsightResponse)
def presentation_current_slide_insight(
    req: CurrentSlideInsightRequest,
) -> CurrentSlideInsightResponse:
    result = current_slide_insight(mode=req.mode, language=req.language)
    return CurrentSlideInsightResponse(
        ok=result.ok,
        mode=req.mode,
        spoken_text=result.message,
        result=result,
    )


@router.post("/open", response_model=PresentationActionResponse)
def presentation_open() -> PresentationActionResponse:
    return _execute("presentation_open_configured")


@router.post("/slideshow/start", response_model=PresentationActionResponse)
def presentation_start_slideshow() -> PresentationActionResponse:
    return _execute("presentation_start_slideshow")


@router.post("/slideshow/next", response_model=PresentationActionResponse)
def presentation_next_slide() -> PresentationActionResponse:
    return _execute("presentation_next_slide")


@router.post("/slideshow/previous", response_model=PresentationActionResponse)
def presentation_previous_slide() -> PresentationActionResponse:
    return _execute("presentation_previous_slide")


@router.post("/slideshow/goto", response_model=PresentationActionResponse)
def presentation_go_to_slide(req: GoToSlideRequest) -> PresentationActionResponse:
    return _execute("presentation_go_to_slide", {"slide_number": req.slide_number})


@router.post("/slideshow/end", response_model=PresentationActionResponse)
def presentation_end_slideshow() -> PresentationActionResponse:
    return _execute("presentation_end_slideshow")


@router.post("/close", response_model=PresentationActionResponse)
def presentation_close(req: ClosePresentationRequest) -> PresentationActionResponse:
    if not req.confirmed:
        raise HTTPException(
            status_code=409,
            detail="Closing PowerPoint without saving changes requires confirmed=true.",
        )
    return _execute("presentation_close")
