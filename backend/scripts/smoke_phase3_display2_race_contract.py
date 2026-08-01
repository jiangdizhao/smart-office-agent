from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import ToolResult, VerificationResult  # noqa: E402
import app.presentation_actions as presentation_actions  # noqa: E402


def main() -> None:
    stale_placement = {
        "target_monitor": {"device": r"\\.\DISPLAY2"},
        "window_found": False,
        "placement_attempted": False,
        "placement_verified": False,
        "observed_monitor_device": None,
        "window_maximized": None,
        "error": "PowerPoint slide-show window was not found yet.",
    }
    final_placement = {
        "target_monitor": {"device": r"\\.\DISPLAY2"},
        "window_found": True,
        "placement_attempted": True,
        "placement_verified": True,
        "hwnd": 12345,
        "observed_monitor_device": r"\\.\DISPLAY2",
        "window_visible": True,
        "window_minimized": False,
        "window_maximized": True,
    }

    placement_calls = 0

    def fake_place(**_kwargs) -> dict:
        nonlocal placement_calls
        placement_calls += 1
        return final_placement

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
                # The first bounded attempt may run before Windows exposes the HWND.
                "window_placement": stale_placement,
                "window_placement_verified": False,
            },
            raw={"window_placement": stale_placement},
        )

    def fake_status() -> ToolResult:
        return ToolResult(
            tool_name="presentation_get_status",
            ok=True,
            message="Status inspected.",
            data={
                "powerpoint_connected": True,
                "powerpoint_process_id": 2468,
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
        "start": presentation_actions.start_configured_slideshow_on_content_display,
        "place": presentation_actions.place_window_on_content_monitor,
        "verify": presentation_actions.verify_presentation_tool_result,
        "status": presentation_actions.get_presentation_status,
    }
    presentation_actions.start_configured_slideshow_on_content_display = fake_start
    presentation_actions.place_window_on_content_monitor = fake_place
    presentation_actions.verify_presentation_tool_result = fake_verify
    presentation_actions.get_presentation_status = fake_status

    try:
        tool, verification, status = presentation_actions.execute_presentation_tool_call(
            "presentation_start_slideshow",
            {},
        )
    finally:
        presentation_actions.start_configured_slideshow_on_content_display = originals["start"]
        presentation_actions.place_window_on_content_monitor = originals["place"]
        presentation_actions.verify_presentation_tool_result = originals["verify"]
        presentation_actions.get_presentation_status = originals["status"]

    assert tool.ok is True
    assert placement_calls == 1
    assert tool.data["window_placement_verified"] is True
    assert tool.data["window_placement"]["hwnd"] == 12345
    assert verification.ok is True
    assert verification.raw["content_display_placement_ok"] is True
    assert status.data["powerpoint_window_monitor_device"] == r"\\.\DISPLAY2"
    assert status.data["monitor_placement_enforced"] is True
    assert status.data["window_maximized"] is True
    assert tool.raw["content_display_placement"]["placement_verified"] is True

    print(
        "PASS: a transient missing PowerPoint slide-show HWND no longer becomes a "
        "permanent content-display failure; the unified placement layer verifies the "
        "freshly discovered, maximized slide-show window."
    )


if __name__ == "__main__":
    main()
