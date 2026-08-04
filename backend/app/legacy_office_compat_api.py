from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from app.office_actions import execute_office_tool_call
from app.office_api import SystemVolumeRequest
from app.sales_phase2a import router as sales_phase2a_router

router = APIRouter(tags=["office-compatibility"])
router.include_router(sales_phase2a_router)


@router.post("/api/office/system/volume")
async def legacy_system_volume(req: SystemVolumeRequest) -> dict[str, Any]:
    """Preserve the original bounded URL through the unified Office worker."""

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
