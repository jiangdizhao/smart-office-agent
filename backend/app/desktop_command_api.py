from __future__ import annotations

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.models import ToolResult
from app.tool_registry import run_tool

router = APIRouter(prefix="/api/desktop-command", tags=["desktop-command"])

DesktopAction = Literal[
    "open_powerpoint",
    "close_powerpoint",
    "open_teams",
    "close_teams",
    "open_onenote",
    "close_onenote",
    "play_music",
    "stop_music",
]

_ACTION_TO_TOOL: dict[DesktopAction, str] = {
    "open_powerpoint": "presentation_open_configured",
    "close_powerpoint": "presentation_close",
    "open_teams": "system_open_teams",
    "close_teams": "system_close_teams",
    "open_onenote": "system_open_onenote",
    "close_onenote": "system_close_onenote",
    "play_music": "system_music_play_random",
    "stop_music": "system_music_stop",
}


class DesktopCommandRequest(BaseModel):
    action: DesktopAction
    command_id: str | None = Field(default=None, min_length=1, max_length=240)


class DesktopCommandResponse(BaseModel):
    action: DesktopAction
    command_id: str
    tool_name: str
    result: ToolResult


@router.post("", response_model=DesktopCommandResponse)
def execute_desktop_command(req: DesktopCommandRequest) -> DesktopCommandResponse:
    tool_name = _ACTION_TO_TOOL[req.action]
    command_id = (req.command_id or f"desktop-{uuid4().hex}").strip()
    result = run_tool(tool_name, {"_command_id": command_id})
    return DesktopCommandResponse(
        action=req.action,
        command_id=command_id,
        tool_name=tool_name,
        result=result,
    )
