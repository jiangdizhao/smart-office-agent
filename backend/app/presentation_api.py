from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models import ToolResult, VerificationResult
from app.presentation_actions import execute_presentation_tool_call
from app.presentation_config import presentation_config
from app.tools.presentation_controller import get_presentation_status


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
