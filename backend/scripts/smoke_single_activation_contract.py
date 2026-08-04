from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import ToolResult  # noqa: E402
from app import tool_registry  # noqa: E402


def _fake_result(tool_name: str, target: str) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        ok=True,
        message=f"fake activation for {target}",
        data={"verified": True, "target": target},
    )


def _exercise(
    *,
    tool_name: str,
    global_name: str,
    fake: Callable[..., ToolResult],
    command_id: str,
) -> None:
    original = getattr(tool_registry, global_name)
    calls = {"count": 0}

    def counted(*args, **kwargs):
        calls["count"] += 1
        return fake(*args, **kwargs)

    setattr(tool_registry, global_name, counted)
    try:
        with tool_registry._CACHE_LOCK:
            tool_registry._ACTIVATION_CACHE.clear()
            tool_registry._COMMAND_CACHE.clear()
        results = [
            tool_registry.run_tool(tool_name, {"_command_id": command_id})
            for _ in range(4)
        ]
        assert calls["count"] == 1, (tool_name, calls)
        assert results[0].data["idempotent_replay"] is False
        assert all(result.data["activation_policy"] == "single_immediate_activation" for result in results)
        assert all(result.data["delayed_reactivation_allowed"] is False for result in results)
        assert all(result.data["command_id"] == command_id for result in results)
        assert all(result.data["idempotent_replay"] is True for result in results[1:])
    finally:
        setattr(tool_registry, global_name, original)
        with tool_registry._CACHE_LOCK:
            tool_registry._ACTIVATION_CACHE.clear()
            tool_registry._COMMAND_CACHE.clear()


def main() -> None:
    _exercise(
        tool_name="system_open_teams",
        global_name="open_managed_application_on_content_display",
        fake=lambda application: _fake_result("system_open_teams", application),
        command_id="visit-1:turn-1:teams-open",
    )
    _exercise(
        tool_name="system_open_onenote",
        global_name="open_managed_application_on_content_display",
        fake=lambda application: _fake_result("system_open_onenote", application),
        command_id="visit-1:turn-2:onenote-open",
    )
    _exercise(
        tool_name="presentation_open_configured",
        global_name="open_configured_presentation_on_content_display",
        fake=lambda: _fake_result("presentation_open_configured", "powerpoint"),
        command_id="visit-1:turn-3:powerpoint-open",
    )
    _exercise(
        tool_name="system_music_play_random",
        global_name="play_random_music_on_content_display",
        fake=lambda: _fake_result("system_music_play_random", "media-player"),
        command_id="visit-1:turn-4:music-play",
    )
    _exercise(
        tool_name="open_word",
        global_name="open_word",
        fake=lambda: _fake_result("open_word", "word"),
        command_id="visit-1:turn-5:word-open",
    )

    managed = (
        BACKEND_DIR / "app" / "tools" / "managed_desktop_actions.py"
    ).read_text(encoding="utf-8")
    assert "single immediate protocol activation" in managed
    assert "second_reactivation" not in managed
    assert "time.sleep(0.6)" not in managed
    assert "time.sleep(0.8)" not in managed
    assert '"delayed_reactivation_allowed": False' in managed
    assert '"activation_attempt_count"' in managed

    outlook_drafts = (BACKEND_DIR / "app" / "outlook_drafts.py").read_text(encoding="utf-8")
    assert outlook_drafts.count("saved.Display(False)") == 1
    outlook_send = (BACKEND_DIR / "app" / "outlook_send.py").read_text(encoding="utf-8")
    assert outlook_send.count("explorer.Display()") == 1

    voice_loop = (
        REPO_ROOT
        / "ui"
        / "smart-office-ui"
        / "src"
        / "vision"
        / "proactiveReceptionVoiceLoop.ts"
    ).read_text(encoding="utf-8")
    assert "unified_semantic_router" in voice_loop
    assert "duplicate-transcript-suppressed" in voice_loop
    assert "await controller().submit(transcript, 'voice')" in voice_loop
    assert "executeDeterministicDesktopCommand" not in voice_loop
    assert "resolveInteractionVoiceCommand" not in voice_loop
    assert "/api/desktop-command" not in voice_loop

    print(
        "PASS: continuous voice has one Unified Router owner; duplicate transcripts "
        "are suppressed; Teams, OneNote, PowerPoint, Media Player and Word activation "
        "calls are idempotent; managed applications never perform delayed reactivation; "
        "and Outlook uses one visible Display call per draft/send path."
    )


if __name__ == "__main__":
    main()
