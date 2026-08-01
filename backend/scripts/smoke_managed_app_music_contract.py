from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import ToolResult  # noqa: E402
from app.planner import plan_task  # noqa: E402
import app.tool_registry as tool_registry  # noqa: E402


def _tool(text: str) -> str | None:
    steps = plan_task(text)
    assert len(steps) == 1, (text, steps)
    return steps[0].tool_name


def main() -> None:
    assert _tool("播放音乐") == "system_music_play_random"
    assert _tool("关闭音乐") == "system_music_stop"
    assert _tool("打开 Teams") == "system_open_teams"
    assert _tool("关闭 Microsoft Teams") == "system_close_teams"
    assert _tool("打开 OneNote") == "system_open_onenote"
    assert _tool("退出 one note") == "system_close_onenote"
    assert _tool("打开 PowerPoint") == "presentation_open_configured"
    assert _tool("关闭 PPT") == "presentation_close"

    compound = plan_task("准备 Teams 会议并打开 PowerPoint 演示")
    assert len(compound) > 1
    assert any(step.requires_confirmation for step in compound)

    original_open = tool_registry.open_managed_application_on_content_display
    original_close = tool_registry.close_managed_application_from_desktop
    original_play = tool_registry.play_random_music_on_content_display
    original_stop = tool_registry.stop_music_from_desktop
    original_ppt_open = tool_registry.open_configured_presentation_on_content_display
    original_ppt_close = tool_registry.close_powerpoint_discarding_changes
    try:
        tool_registry.open_managed_application_on_content_display = lambda app: ToolResult(
            tool_name=f"system_open_{app}",
            ok=True,
            message=f"{app} opened",
            data={
                "application": app,
                "action": "open",
                "window_placement_verified": True,
                "verified": True,
            },
        )
        tool_registry.close_managed_application_from_desktop = lambda app: ToolResult(
            tool_name=f"system_close_{app}",
            ok=True,
            message=f"{app} closed",
            data={"application": app, "action": "close", "verified": True},
        )
        tool_registry.play_random_music_on_content_display = lambda: ToolResult(
            tool_name="system_music_play_random",
            ok=True,
            message="music started",
            data={
                "action": "play_random",
                "selected_track_name": "demo.mp3",
                "window_placement_verified": True,
                "verified": True,
            },
        )
        tool_registry.stop_music_from_desktop = lambda: ToolResult(
            tool_name="system_music_stop",
            ok=True,
            message="music stopped",
            data={"action": "stop", "verified": True},
        )
        tool_registry.open_configured_presentation_on_content_display = lambda: ToolResult(
            tool_name="presentation_open_configured",
            ok=True,
            message="PowerPoint opened",
            data={
                "requested_state": {"presentation_open": True},
                "window_placement_verified": True,
                "verified": True,
            },
        )
        tool_registry.close_powerpoint_discarding_changes = lambda: ToolResult(
            tool_name="presentation_close",
            ok=True,
            message="PowerPoint closed",
            data={
                "requested_state": {"presentation_open": False},
                "discard_unsaved_changes": True,
                "remaining_powerpoint_pids": [],
                "verified": True,
            },
        )

        cases = {
            "system_open_teams": ("teams", "open"),
            "system_close_teams": ("teams", "close"),
            "system_open_onenote": ("onenote", "open"),
            "system_close_onenote": ("onenote", "close"),
        }
        for tool_name, expected in cases.items():
            result = tool_registry.run_tool(tool_name, {})
            assert result.ok is True
            assert (result.data["application"], result.data["action"]) == expected
            assert result.data["verified"] is True

        play = tool_registry.run_tool("system_music_play_random", {})
        assert play.ok is True
        assert play.data["selected_track_name"] == "demo.mp3"
        assert play.data["window_placement_verified"] is True

        stop = tool_registry.run_tool("system_music_stop", {})
        assert stop.ok is True
        assert stop.data["verified"] is True

        ppt_open = tool_registry.run_tool("presentation_open_configured", {})
        assert ppt_open.ok is True
        assert ppt_open.data["window_placement_verified"] is True

        ppt_close = tool_registry.run_tool("presentation_close", {})
        assert ppt_close.ok is True
        assert ppt_close.data["discard_unsaved_changes"] is True
        assert ppt_close.data["remaining_powerpoint_pids"] == []
    finally:
        tool_registry.open_managed_application_on_content_display = original_open
        tool_registry.close_managed_application_from_desktop = original_close
        tool_registry.play_random_music_on_content_display = original_play
        tool_registry.stop_music_from_desktop = original_stop
        tool_registry.open_configured_presentation_on_content_display = original_ppt_open
        tool_registry.close_powerpoint_discarding_changes = original_ppt_close

    print(
        "PASS: deterministic app, music, right-display and PowerPoint close dispatch are healthy."
    )


if __name__ == "__main__":
    main()
