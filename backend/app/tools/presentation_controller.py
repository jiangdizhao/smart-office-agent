from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from app.models import ToolResult
from app.presentation_config import PresentationRuntimeConfig, presentation_config


POWERPOINT_PROCESS_NAMES = ["POWERPNT.EXE"]
POWERPOINT_WINDOW_KEYWORDS = ["PowerPoint"]


class PowerPointController:
    """Controlled Microsoft PowerPoint automation for the configured demo file.

    Every public method enters its own COM apartment. COM objects are never stored
    across requests or worker threads; each action reconnects to the active
    PowerPoint application and resolves the configured presentation by full path.
    """

    def __init__(self, config: PresentationRuntimeConfig = presentation_config) -> None:
        self.config = config
        self._lock = RLock()

    @staticmethod
    def _operation_id() -> str:
        return str(uuid4())

    @staticmethod
    def _path_key(value: str | Path) -> str:
        return os.path.normcase(os.path.abspath(str(value)))

    @contextmanager
    def _com_application(self, *, create: bool) -> Iterator[Any | None]:
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            yield None
            return

        pythoncom.CoInitialize()
        application = None
        try:
            try:
                application = win32com.client.GetActiveObject("PowerPoint.Application")
            except Exception:
                if create:
                    application = win32com.client.Dispatch("PowerPoint.Application")
            yield application
        finally:
            application = None
            pythoncom.CoUninitialize()

    def _unsupported_result(self, tool_name: str, message: str) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=message,
            expected_process_names=POWERPOINT_PROCESS_NAMES,
            expected_window_keywords=POWERPOINT_WINDOW_KEYWORDS,
            data={
                "operation_id": self._operation_id(),
                "execution_mode": "unsupported",
                "configured_presentation": str(self.config.presentation_path),
            },
        )

    def _failure_result(
        self,
        tool_name: str,
        message: str,
        *,
        error: Exception | None = None,
        requested_state: dict | None = None,
        observed_before: dict | None = None,
    ) -> ToolResult:
        raw: dict[str, Any] = {}
        if error is not None:
            raw = {"error_type": type(error).__name__, "error": str(error)}
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=message,
            expected_process_names=POWERPOINT_PROCESS_NAMES,
            expected_window_keywords=POWERPOINT_WINDOW_KEYWORDS,
            data={
                "operation_id": self._operation_id(),
                "execution_mode": "real",
                "configured_presentation": str(self.config.presentation_path),
                "requested_state": requested_state or {},
                "observed_before": observed_before or {},
            },
            raw=raw,
        )

    def _success_result(
        self,
        tool_name: str,
        message: str,
        *,
        requested_state: dict,
        observed_before: dict,
        raw: dict | None = None,
        artifacts: list[str] | None = None,
        started_at: float,
    ) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            ok=True,
            message=message,
            expected_process_names=POWERPOINT_PROCESS_NAMES,
            expected_window_keywords=POWERPOINT_WINDOW_KEYWORDS,
            artifacts=artifacts or [],
            data={
                "operation_id": self._operation_id(),
                "execution_mode": "real",
                "configured_presentation": str(self.config.presentation_path),
                "requested_state": requested_state,
                "observed_before": observed_before,
                "duration_ms": round((time.monotonic() - started_at) * 1000),
            },
            raw=raw or {},
        )

    def _find_configured_presentation(self, application: Any) -> Any | None:
        configured_key = self._path_key(self.config.presentation_path)
        presentations = application.Presentations
        for index in range(1, int(presentations.Count) + 1):
            presentation = presentations.Item(index)
            try:
                if self._path_key(str(presentation.FullName)) == configured_key:
                    return presentation
            except Exception:
                continue
        return None

    def _find_slideshow_window(self, application: Any, presentation: Any) -> Any | None:
        configured_key = self._path_key(str(presentation.FullName))
        windows = application.SlideShowWindows
        for index in range(1, int(windows.Count) + 1):
            window = windows.Item(index)
            try:
                if self._path_key(str(window.Presentation.FullName)) == configured_key:
                    return window
            except Exception:
                continue
        return None

    @staticmethod
    def _application_pid(application: Any) -> int | None:
        try:
            import win32process

            _thread_id, process_id = win32process.GetWindowThreadProcessId(
                int(application.HWND)
            )
            return int(process_id)
        except Exception:
            return None

    def _state_from_application(self, application: Any | None) -> dict[str, Any]:
        base_state: dict[str, Any] = {
            "configured_presentation": str(self.config.presentation_path),
            "presentation_file_exists": self.config.presentation_path.is_file(),
            "powerpoint_connected": False,
            "powerpoint_process_id": None,
            "powerpoint_version": None,
            "presentation_open": False,
            "presentation_name": self.config.presentation_path.name,
            "presentation_read_only": None,
            "slideshow_active": False,
            "current_slide": None,
            "total_slides": None,
            "open_presentations": [],
            "target_monitor_device": self.config.target_monitor_device,
            "target_monitor_number": self.config.target_monitor_number,
            "monitor_placement_enforced": False,
        }
        if application is None:
            base_state["execution_mode"] = "unsupported"
            return base_state

        base_state["execution_mode"] = "real"
        base_state["powerpoint_connected"] = True
        base_state["powerpoint_process_id"] = self._application_pid(application)
        try:
            base_state["powerpoint_version"] = str(application.Version)
        except Exception:
            pass

        try:
            presentations = application.Presentations
            open_presentations: list[dict[str, str]] = []
            for index in range(1, int(presentations.Count) + 1):
                item = presentations.Item(index)
                open_presentations.append(
                    {"name": str(item.Name), "full_name": str(item.FullName)}
                )
            base_state["open_presentations"] = open_presentations
        except Exception:
            pass

        presentation = self._find_configured_presentation(application)
        if presentation is None:
            return base_state

        base_state["presentation_open"] = True
        try:
            base_state["presentation_name"] = str(presentation.Name)
            base_state["total_slides"] = int(presentation.Slides.Count)
            base_state["presentation_read_only"] = bool(presentation.ReadOnly)
        except Exception:
            pass

        window = self._find_slideshow_window(application, presentation)
        if window is None:
            return base_state

        base_state["slideshow_active"] = True
        try:
            base_state["current_slide"] = int(window.View.CurrentShowPosition)
        except Exception:
            pass
        return base_state

    def get_status(self) -> ToolResult:
        with self._lock:
            with self._com_application(create=False) as application:
                state = self._state_from_application(application)
        mode = state["execution_mode"]
        message = (
            "PowerPoint presentation status inspected."
            if mode == "real"
            else "PowerPoint COM is unavailable on this runtime."
        )
        return ToolResult(
            tool_name="presentation_get_status",
            ok=True,
            message=message,
            launched_pid=state.get("powerpoint_process_id"),
            expected_process_names=POWERPOINT_PROCESS_NAMES,
            expected_window_keywords=POWERPOINT_WINDOW_KEYWORDS,
            data=state,
        )

    def open_configured(self) -> ToolResult:
        tool_name = "presentation_open_configured"
        started_at = time.monotonic()
        path = self.config.presentation_path
        if not path.is_file():
            return self._failure_result(
                tool_name,
                f"Configured presentation was not found: {path}",
                requested_state={"presentation_open": True},
            )
        if path.suffix.casefold() not in {".ppt", ".pptx", ".pptm", ".ppsx", ".ppsm"}:
            return self._failure_result(
                tool_name,
                f"Configured file is not a supported PowerPoint presentation: {path.name}",
                requested_state={"presentation_open": True},
            )

        with self._lock:
            try:
                with self._com_application(create=True) as application:
                    if application is None:
                        return self._unsupported_result(
                            tool_name,
                            "PowerPoint COM requires Windows, pywin32, and Microsoft PowerPoint.",
                        )
                    application.Visible = True
                    observed_before = self._state_from_application(application)
                    presentation = self._find_configured_presentation(application)
                    if presentation is None:
                        presentation = application.Presentations.Open(
                            str(path),
                            ReadOnly=-1,
                            Untitled=0,
                            WithWindow=-1,
                        )
                    try:
                        presentation.Windows.Item(1).Activate()
                    except Exception:
                        pass
                    pid = self._application_pid(application)
                    return self._success_result(
                        tool_name,
                        f"Opened configured presentation: {path.name}",
                        requested_state={
                            "presentation_open": True,
                            "presentation_path": str(path),
                        },
                        observed_before=observed_before,
                        raw={"com_method": "Presentations.Open", "read_only": True},
                        artifacts=[str(path)],
                        started_at=started_at,
                    ).model_copy(update={"launched_pid": pid})
            except Exception as exc:
                return self._failure_result(
                    tool_name,
                    f"Failed to open configured presentation: {exc}",
                    error=exc,
                    requested_state={"presentation_open": True},
                )

    def start_slideshow(self) -> ToolResult:
        tool_name = "presentation_start_slideshow"
        started_at = time.monotonic()
        with self._lock:
            try:
                with self._com_application(create=True) as application:
                    if application is None:
                        return self._unsupported_result(
                            tool_name,
                            "PowerPoint COM requires Windows, pywin32, and Microsoft PowerPoint.",
                        )
                    application.Visible = True
                    presentation = self._find_configured_presentation(application)
                    if presentation is None:
                        if not self.config.presentation_path.is_file():
                            return self._failure_result(
                                tool_name,
                                f"Configured presentation was not found: {self.config.presentation_path}",
                                requested_state={"slideshow_active": True},
                            )
                        presentation = application.Presentations.Open(
                            str(self.config.presentation_path),
                            ReadOnly=-1,
                            Untitled=0,
                            WithWindow=-1,
                        )
                    observed_before = self._state_from_application(application)
                    existing_window = self._find_slideshow_window(application, presentation)
                    if existing_window is None:
                        settings = presentation.SlideShowSettings
                        settings.ShowType = 1
                        settings.Run()
                    return self._success_result(
                        tool_name,
                        "Started the configured PowerPoint slide show.",
                        requested_state={"slideshow_active": True, "current_slide": 1},
                        observed_before=observed_before,
                        raw={"com_method": "SlideShowSettings.Run"},
                        artifacts=[str(self.config.presentation_path)],
                        started_at=started_at,
                    )
            except Exception as exc:
                return self._failure_result(
                    tool_name,
                    f"Failed to start slide show: {exc}",
                    error=exc,
                    requested_state={"slideshow_active": True},
                )

    def _active_slideshow(self, application: Any) -> tuple[Any | None, Any | None, dict]:
        presentation = self._find_configured_presentation(application)
        state = self._state_from_application(application)
        if presentation is None:
            return None, None, state
        return presentation, self._find_slideshow_window(application, presentation), state

    def next_slide(self) -> ToolResult:
        tool_name = "presentation_next_slide"
        started_at = time.monotonic()
        with self._lock:
            try:
                with self._com_application(create=False) as application:
                    if application is None:
                        return self._unsupported_result(
                            tool_name,
                            "PowerPoint COM is unavailable or PowerPoint is not running.",
                        )
                    _presentation, window, observed_before = self._active_slideshow(application)
                    if window is None:
                        return self._failure_result(
                            tool_name,
                            "The configured presentation is not currently in slide-show mode.",
                            requested_state={"action": "next_slide"},
                            observed_before=observed_before,
                        )
                    current = int(window.View.CurrentShowPosition)
                    total = int(observed_before.get("total_slides") or 0)
                    if current >= total:
                        return self._failure_result(
                            tool_name,
                            "The slide show is already on the last slide; Next was not sent because speaker mode would close the show.",
                            requested_state={"current_slide": current},
                            observed_before=observed_before,
                        )
                    window.View.Next()
                    return self._success_result(
                        tool_name,
                        f"Requested next slide: {current} -> {current + 1}.",
                        requested_state={"current_slide": current + 1},
                        observed_before=observed_before,
                        raw={"com_method": "SlideShowView.Next"},
                        started_at=started_at,
                    )
            except Exception as exc:
                return self._failure_result(
                    tool_name,
                    f"Failed to move to the next slide: {exc}",
                    error=exc,
                    requested_state={"action": "next_slide"},
                )

    def previous_slide(self) -> ToolResult:
        tool_name = "presentation_previous_slide"
        started_at = time.monotonic()
        with self._lock:
            try:
                with self._com_application(create=False) as application:
                    if application is None:
                        return self._unsupported_result(
                            tool_name,
                            "PowerPoint COM is unavailable or PowerPoint is not running.",
                        )
                    _presentation, window, observed_before = self._active_slideshow(application)
                    if window is None:
                        return self._failure_result(
                            tool_name,
                            "The configured presentation is not currently in slide-show mode.",
                            requested_state={"action": "previous_slide"},
                            observed_before=observed_before,
                        )
                    current = int(window.View.CurrentShowPosition)
                    if current <= 1:
                        return self._failure_result(
                            tool_name,
                            "The slide show is already on the first slide.",
                            requested_state={"current_slide": 1},
                            observed_before=observed_before,
                        )
                    window.View.Previous()
                    return self._success_result(
                        tool_name,
                        f"Requested previous slide: {current} -> {current - 1}.",
                        requested_state={"current_slide": current - 1},
                        observed_before=observed_before,
                        raw={"com_method": "SlideShowView.Previous"},
                        started_at=started_at,
                    )
            except Exception as exc:
                return self._failure_result(
                    tool_name,
                    f"Failed to move to the previous slide: {exc}",
                    error=exc,
                    requested_state={"action": "previous_slide"},
                )

    def go_to_slide(self, slide_number: int) -> ToolResult:
        tool_name = "presentation_go_to_slide"
        started_at = time.monotonic()
        with self._lock:
            try:
                with self._com_application(create=False) as application:
                    if application is None:
                        return self._unsupported_result(
                            tool_name,
                            "PowerPoint COM is unavailable or PowerPoint is not running.",
                        )
                    _presentation, window, observed_before = self._active_slideshow(application)
                    if window is None:
                        return self._failure_result(
                            tool_name,
                            "The configured presentation is not currently in slide-show mode.",
                            requested_state={"current_slide": slide_number},
                            observed_before=observed_before,
                        )
                    total = int(observed_before.get("total_slides") or 0)
                    if slide_number < 1 or slide_number > total:
                        return self._failure_result(
                            tool_name,
                            f"Slide number must be between 1 and {total}; received {slide_number}.",
                            requested_state={"current_slide": slide_number},
                            observed_before=observed_before,
                        )
                    window.View.GotoSlide(int(slide_number), -1)
                    return self._success_result(
                        tool_name,
                        f"Requested slide {slide_number}.",
                        requested_state={"current_slide": slide_number},
                        observed_before=observed_before,
                        raw={
                            "com_method": "SlideShowView.GotoSlide",
                            "reset_slide": True,
                        },
                        started_at=started_at,
                    )
            except Exception as exc:
                return self._failure_result(
                    tool_name,
                    f"Failed to go to slide {slide_number}: {exc}",
                    error=exc,
                    requested_state={"current_slide": slide_number},
                )

    def end_slideshow(self) -> ToolResult:
        tool_name = "presentation_end_slideshow"
        started_at = time.monotonic()
        with self._lock:
            try:
                with self._com_application(create=False) as application:
                    if application is None:
                        return self._unsupported_result(
                            tool_name,
                            "PowerPoint COM is unavailable or PowerPoint is not running.",
                        )
                    _presentation, window, observed_before = self._active_slideshow(application)
                    if window is None:
                        return self._success_result(
                            tool_name,
                            "The configured presentation was already outside slide-show mode.",
                            requested_state={"slideshow_active": False},
                            observed_before=observed_before,
                            raw={"idempotent": True},
                            started_at=started_at,
                        )
                    window.View.Exit()
                    return self._success_result(
                        tool_name,
                        "Ended the configured PowerPoint slide show.",
                        requested_state={"slideshow_active": False},
                        observed_before=observed_before,
                        raw={"com_method": "SlideShowView.Exit"},
                        started_at=started_at,
                    )
            except Exception as exc:
                return self._failure_result(
                    tool_name,
                    f"Failed to end slide show: {exc}",
                    error=exc,
                    requested_state={"slideshow_active": False},
                )

    def close_configured(self) -> ToolResult:
        tool_name = "presentation_close"
        started_at = time.monotonic()
        with self._lock:
            try:
                with self._com_application(create=False) as application:
                    if application is None:
                        return self._unsupported_result(
                            tool_name,
                            "PowerPoint COM is unavailable or PowerPoint is not running.",
                        )
                    presentation = self._find_configured_presentation(application)
                    observed_before = self._state_from_application(application)
                    if presentation is None:
                        return self._success_result(
                            tool_name,
                            "The configured presentation was already closed.",
                            requested_state={"presentation_open": False},
                            observed_before=observed_before,
                            raw={"idempotent": True},
                            started_at=started_at,
                        )
                    window = self._find_slideshow_window(application, presentation)
                    if window is not None:
                        window.View.Exit()
                    if not bool(presentation.Saved):
                        return self._failure_result(
                            tool_name,
                            "The presentation has unsaved changes and was not closed.",
                            requested_state={"presentation_open": False},
                            observed_before=observed_before,
                        )
                    presentation.Close()
                    if (
                        self.config.close_powerpoint_when_empty
                        and int(application.Presentations.Count) == 0
                    ):
                        application.Quit()
                    return self._success_result(
                        tool_name,
                        "Closed the configured presentation.",
                        requested_state={"presentation_open": False},
                        observed_before=observed_before,
                        raw={"com_method": "Presentation.Close"},
                        started_at=started_at,
                    )
            except Exception as exc:
                return self._failure_result(
                    tool_name,
                    f"Failed to close configured presentation: {exc}",
                    error=exc,
                    requested_state={"presentation_open": False},
                )


presentation_controller = PowerPointController()


def get_presentation_status() -> ToolResult:
    return presentation_controller.get_status()


def open_configured_presentation() -> ToolResult:
    return presentation_controller.open_configured()


def start_configured_slideshow() -> ToolResult:
    return presentation_controller.start_slideshow()


def next_presentation_slide() -> ToolResult:
    return presentation_controller.next_slide()


def previous_presentation_slide() -> ToolResult:
    return presentation_controller.previous_slide()


def go_to_presentation_slide(slide_number: int) -> ToolResult:
    return presentation_controller.go_to_slide(slide_number)


def end_configured_slideshow() -> ToolResult:
    return presentation_controller.end_slideshow()


def close_configured_presentation() -> ToolResult:
    return presentation_controller.close_configured()
