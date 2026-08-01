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

    original_open = tool_registry.open_managed_application
    original_close = tool_registry.close_managed_application
    original_play = tool_registry.play_random_music
    original_stop = tool_registry.stop_music
    try:
        tool_registry.open_managed_application = lambda app: ToolResult(
            tool_name=f"system_open_{app}",
            ok=True,
            message=f"{app} opened",
            data={"application": app, "action": "open", "verified": True},
        )
        tool_registry.close_managed_application = lambda app: ToolResult(
            tool_name=f"system_close_{app}",
            ok=True,
            message=f"{app} closed",
            data={"application": app, "action": "close", "verified": True},
        )
        tool_registry.play_random_music = lambda: ToolResult(
            tool_name="system_music_play_random",
            ok=True,
            message="music started",
            data={
                "action": "play_random",
                "selected_track_name": "demo.mp3",
                "verified": True,
            },
        )
        tool_registry.stop_music = lambda: ToolResult(
            tool_name="system_music_stop",
            ok=True,
            message="music stopped",
            data={"action": "stop", "verified": True},
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
        assert play.data["verified"] is True

        stop = tool_registry.run_tool("system_music_stop", {})
        assert stop.ok is True
        assert stop.data["verified"] is True
    finally:
        tool_registry.open_managed_application = original_open
        tool_registry.close_managed_application = original_close
        tool_registry.play_random_music = original_play
        tool_registry.stop_music = original_stop

    print(
        "PASS: deterministic music, Teams and OneNote planning and tool dispatch are healthy."
    )


if __name__ == "__main__":
    main()
