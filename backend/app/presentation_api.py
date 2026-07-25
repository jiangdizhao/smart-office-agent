from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models import ToolResult, VerificationResult
from app.presentation_config import presentation_config
from app.presentation_verifier import verify_presentation_tool_result
from app.tools.presentation_controller import (
    close_configured_presentation,
    end_configured_slideshow,
    get_presentation_status,
    go_to_presentation_slide,
    next_presentation_slide,
    open_configured_presentation,
    previous_presentation_slide,
    start_configured_slideshow,
)


router = APIRouter(prefix="/api/presentation", tags=["presentation-gate1"])


class GoToSlideRequest(BaseModel):
    slide_number: int = Field(..., ge=1, le=100_000)


class ClosePresentationRequest(BaseModel):
    confirmed: bool = False


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


def _run_and_verify(tool_result: ToolResult) -> PresentationActionResponse:
    verification = verify_presentation_tool_result(tool_result)
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


@router.post("/open", response_model=PresentationActionResponse)
def presentation_open() -> PresentationActionResponse:
    return _run_and_verify(open_configured_presentation())


@router.post("/slideshow/start", response_model=PresentationActionResponse)
def presentation_start_slideshow() -> PresentationActionResponse:
    return _run_and_verify(start_configured_slideshow())


@router.post("/slideshow/next", response_model=PresentationActionResponse)
def presentation_next_slide() -> PresentationActionResponse:
    return _run_and_verify(next_presentation_slide())


@router.post("/slideshow/previous", response_model=PresentationActionResponse)
def presentation_previous_slide() -> PresentationActionResponse:
    return _run_and_verify(previous_presentation_slide())


@router.post("/slideshow/goto", response_model=PresentationActionResponse)
def presentation_go_to_slide(req: GoToSlideRequest) -> PresentationActionResponse:
    return _run_and_verify(go_to_presentation_slide(req.slide_number))


@router.post("/slideshow/end", response_model=PresentationActionResponse)
def presentation_end_slideshow() -> PresentationActionResponse:
    return _run_and_verify(end_configured_slideshow())


@router.post("/close", response_model=PresentationActionResponse)
def presentation_close(req: ClosePresentationRequest) -> PresentationActionResponse:
    if not req.confirmed:
        raise HTTPException(
            status_code=409,
            detail="Closing the configured presentation requires confirmed=true.",
        )
    return _run_and_verify(close_configured_presentation())
