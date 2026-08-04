from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.office_actions import execute_office_tool_call

router = APIRouter(tags=["office-compatibility"])


class LegacySystemVolumeRequest(BaseModel):
    """Compatibility contract for the original direct volume endpoint.

    The active voice runtime uses the unified Office plan. This adapter preserves
    old debug clients while delegating execution and verification to the same
    isolated Office worker used by the current runtime.
    """

    model_config = ConfigDict(extra="forbid")

    percent: int = Field(..., ge=0, le=100)


@router.post("/api/office/system/volume")
async def legacy_system_volume(req: LegacySystemVolumeRequest) -> dict[str, Any]:
    result, verification, status = await asyncio.to_thread(
        execute_office_tool_call,
        "system_set_volume",
        {"value_percent": req.percent},
    )
    return {
        "ok": bool(result.ok and verification.ok),
        "phase": "m3a_fusion_phase_3_gate_3_5",
        "compatibility_endpoint": True,
        "requested_percent": req.percent,
        "result": result.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json"),
        "office_status": status.model_dump(mode="json"),
        "message": verification.message if result.ok else result.message,
    }
