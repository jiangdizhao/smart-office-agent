from __future__ import annotations

from app.models import ToolResult
import app.presentation_verifier as verifier


def _stale_status() -> ToolResult:
    return ToolResult(
        tool_name="presentation_get_status",
        ok=True,
        message="Status observer has not attached yet.",
        data={
            "execution_mode": "real",
            "powerpoint_connected": False,
            "presentation_open": False,
            "slideshow_active": False,
            "current_slide": None,
            "total_slides": None,
        },
    )


def _successful_action(name: str, method: str, requested_state: dict[str, object]) -> ToolResult:
    return ToolResult(
        tool_name=name,
        ok=True,
        message="Bounded PowerPoint COM action returned successfully.",
        expected_process_names=["POWERPNT.EXE"],
        expected_window_keywords=["PowerPoint"],
        data={
            "execution_mode": "real",
            "requested_state": requested_state,
            "launch_verified": True,
            "window_placement": {
                "window_found": True,
                "window_visible": True,
                "placement_verified": True,
                "selected_window": {
                    "visible": True,
                    "process_name": "POWERPNT.EXE",
                    "title": "Loss.pptx - PowerPoint",
                    "class_name": "PPTFrameClass",
                },
            },
        },
        raw={"com_method": method},
    )


def main() -> None:
    original = verifier.get_presentation_status
    verifier.get_presentation_status = _stale_status
    try:
        opened = verifier.verify_presentation_tool_result(
            _successful_action(
                "presentation_open_configured",
                "Presentations.Open",
                {"presentation_open": True},
            ),
            timeout_seconds=0,
        )
        assert opened.ok, opened
        assert opened.raw.get("verification_source") == "successful_action_result", opened.raw
        assert opened.raw.get("action_result_fallback_used") is True, opened.raw

        started = verifier.verify_presentation_tool_result(
            _successful_action(
                "presentation_start_slideshow",
                "SlideShowSettings.Run",
                {"slideshow_active": True, "current_slide": 1},
            ),
            timeout_seconds=0,
        )
        assert started.ok, started
        assert started.raw.get("verification_source") == "successful_action_result", started.raw

        failed = _successful_action(
            "presentation_open_configured",
            "Presentations.Open",
            {"presentation_open": True},
        ).model_copy(update={"ok": False})
        failure = verifier.verify_presentation_tool_result(failed, timeout_seconds=0)
        assert not failure.ok, failure
        assert failure.raw.get("verification_source") == "tool_failure", failure.raw
    finally:
        verifier.get_presentation_status = original

    print("PASS: successful PowerPoint actions are not contradicted by a stale COM observer")


if __name__ == "__main__":
    main()
