from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import ToolResult, VerificationResult  # noqa: E402
import app.presentation_actions as presentation_actions  # noqa: E402


class FakeBootstrap:
    def to_dict(self) -> dict:
        return {"ok": True, "already_running": True}


def main() -> None:
    stale_monitor = {
        "target_monitor_device": r"\\.\DISPLAY2",
        "target_monitor_available": True,
        "slideshow_window_hwnd": None,
        "slideshow_monitor_device": None,
        "monitor_placement_enforced": False,
        "monitor_error": "PowerPoint slide-show window was not found.",
    }
    final_monitor = {
        "target_monitor_device": r"\\.\DISPLAY2",
        "target_monitor_available": True,
        "slideshow_window_hwnd": 12345,
        "slideshow_monitor_device": r"\\.\DISPLAY2",
        "monitor_placement_enforced": True,
    }

    placement_calls = 0
    inspection_calls = 0

    def fake_place(**_kwargs) -> dict:
        nonlocal placement_calls
        placement_calls += 1
        return stale_monitor if placement_calls == 1 else final_monitor

    def fake_inspect(**_kwargs) -> dict:
        nonlocal inspection_calls
        inspection_calls += 1
        return stale_monitor if inspection_calls == 1 else final_monitor

    def fake_start() -> ToolResult:
        return ToolResult(
            tool_name="presentation_start_slideshow",
            ok=True,
            message="Started the configured PowerPoint slide show.",
            expected_process_names=["POWERPNT.EXE"],
            expected_window_keywords=["PowerPoint"],
            data={
                "execution_mode": "real",
                "requested_state": {
                    "slideshow_active": True,
                    "current_slide": 1,
                },
            },
        )

    def fake_status() -> ToolResult:
        return ToolResult(
            tool_name="presentation_get_status",
            ok=True,
            message="Status inspected.",
            data={
                "powerpoint_connected": True,
                "presentation_open": True,
                "slideshow_active": True,
                "current_slide": 1,
                "total_slides": 9,
            },
        )

    def fake_verify(_result: ToolResult) -> VerificationResult:
        return VerificationResult(
            ok=True,
            message="Slide show is active.",
            process_ok=True,
            window_ok=True,
            checked_at=datetime.now(UTC),
            raw={},
        )

    originals = {
        "bootstrap": presentation_actions.ensure_powerpoint_desktop_running,
        "start": presentation_actions.start_configured_slideshow,
        "place": presentation_actions.place_slideshow_on_target_monitor,
        "inspect": presentation_actions.inspect_slideshow_monitor,
        "verify": presentation_actions.verify_presentation_tool_result,
        "status": presentation_actions.get_presentation_status,
    }
    presentation_actions.ensure_powerpoint_desktop_running = lambda: FakeBootstrap()
    presentation_actions.start_configured_slideshow = fake_start
    presentation_actions.place_slideshow_on_target_monitor = fake_place
    presentation_actions.inspect_slideshow_monitor = fake_inspect
    presentation_actions.verify_presentation_tool_result = fake_verify
    presentation_actions.get_presentation_status = fake_status

    try:
        tool, verification, status = presentation_actions.execute_presentation_tool_call(
            "presentation_start_slideshow",
            {},
        )
    finally:
        presentation_actions.ensure_powerpoint_desktop_running = originals["bootstrap"]
        presentation_actions.start_configured_slideshow = originals["start"]
        presentation_actions.place_slideshow_on_target_monitor = originals["place"]
        presentation_actions.inspect_slideshow_monitor = originals["inspect"]
        presentation_actions.verify_presentation_tool_result = originals["verify"]
        presentation_actions.get_presentation_status = originals["status"]

    assert tool.ok is True
    assert placement_calls == 2
    assert inspection_calls >= 2
    assert verification.ok is True
    assert verification.raw["monitor_ok"] is True
    assert status.data["slideshow_window_hwnd"] == 12345
    assert status.data["slideshow_monitor_device"] == r"\\.\DISPLAY2"
    assert status.data["monitor_placement_enforced"] is True
    assert "monitor_placement_retry" in tool.raw

    print(
        "PASS: a transient missing PowerPoint slide-show HWND no longer becomes a "
        "permanent DISPLAY2 verification failure; final state is freshly inspected."
    )


if __name__ == "__main__":
    main()
