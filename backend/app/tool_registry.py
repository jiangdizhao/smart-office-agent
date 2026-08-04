from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any

from app.desktop_integration_bootstrap import install_desktop_integration_wrappers
from app.models import ToolResult
from app.tools.managed_desktop_actions import (
    close_managed_application_from_desktop,
    open_managed_application_on_content_display,
    play_random_music_on_content_display,
    stop_music_from_desktop,
)
from app.tools.presentation_controller import (
    end_configured_slideshow,
    get_presentation_status,
    go_to_presentation_slide,
    next_presentation_slide,
    previous_presentation_slide,
)
from app.tools.presentation_desktop_actions import (
    close_powerpoint_discarding_changes,
    open_configured_presentation_on_content_display,
    start_configured_slideshow_on_content_display,
)
from app.tools.windows_controller import (
    open_edge,
    open_zoom,
    open_word,
    open_excel,
    open_powerpoint,
    open_onenote,
    open_sample_document,
)

install_desktop_integration_wrappers()

DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0
MANAGED_APPLICATION_TIMEOUT_SECONDS = 32.0
POWERPOINT_DESKTOP_TIMEOUT_SECONDS = 32.0
ACTIVATION_REPLAY_SECONDS = 2.5
COMMAND_RESULT_SECONDS = 60.0

# Every application-opening tool has one target lock. The first request executes
# immediately. Concurrent or near-duplicate requests wait for that first result and
# replay it; they never issue a second delayed launch or activation.
_ACTIVATION_TARGETS: dict[str, str] = {
    "system_open_teams": "teams",
    "system_open_onenote": "onenote",
    "system_music_play_random": "music_player",
    "presentation_open_configured": "powerpoint",
    "presentation_start_slideshow": "powerpoint",
    "open_powerpoint": "powerpoint",
    "open_onenote": "onenote",
    "open_word": "word",
    "open_excel": "excel",
    "open_edge": "edge",
    "open_zoom": "zoom",
    "open_sample_document": "sample_document",
}
_TARGET_LOCKS: dict[str, threading.RLock] = {
    target: threading.RLock() for target in set(_ACTIVATION_TARGETS.values())
}
_CACHE_LOCK = threading.RLock()
_ACTIVATION_CACHE: dict[str, tuple[float, ToolResult]] = {}
_COMMAND_CACHE: dict[str, tuple[float, ToolResult]] = {}


def _normalise_tool_result(
    tool_name: str,
    result: ToolResult | tuple[bool, str],
    args: dict[str, Any],
    timeout_seconds: float,
) -> ToolResult:
    public_args = {key: value for key, value in args.items() if not key.startswith("_")}
    if isinstance(result, ToolResult):
        return result.model_copy(
            update={
                "data": {
                    **result.data,
                    "args": public_args,
                    "timeout_seconds": timeout_seconds,
                }
            }
        )

    ok, message = result
    return ToolResult(
        tool_name=tool_name,
        ok=ok,
        message=message,
        data={
            "args": public_args,
            "timeout_seconds": timeout_seconds,
        },
    )


def _cache_copy(
    result: ToolResult,
    *,
    target: str,
    command_id: str | None,
    replay: bool,
) -> ToolResult:
    return result.model_copy(
        deep=True,
        update={
            "data": {
                **result.data,
                "activation_policy": "single_immediate_activation",
                "activation_target": target,
                "command_id": command_id,
                "idempotent_replay": replay,
                "delayed_reactivation_allowed": False,
            }
        },
    )


def _purge_cache(now: float) -> None:
    with _CACHE_LOCK:
        for key, (created, _result) in list(_ACTIVATION_CACHE.items()):
            if now - created > ACTIVATION_REPLAY_SECONDS:
                _ACTIVATION_CACHE.pop(key, None)
        for key, (created, _result) in list(_COMMAND_CACHE.items()):
            if now - created > COMMAND_RESULT_SECONDS:
                _COMMAND_CACHE.pop(key, None)


def _activation_key(tool_name: str, args: dict[str, Any]) -> str:
    public_args = {key: value for key, value in args.items() if not key.startswith("_")}
    return f"{tool_name}:{json.dumps(public_args, ensure_ascii=False, sort_keys=True, default=str)}"


def _command_key(tool_name: str, command_id: str | None) -> str | None:
    if not command_id:
        return None
    return f"{command_id}:{tool_name}"


def _cached_result(
    *,
    tool_name: str,
    activation_key: str,
    command_id: str | None,
    target: str,
    now: float,
) -> ToolResult | None:
    with _CACHE_LOCK:
        command_key = _command_key(tool_name, command_id)
        if command_key:
            cached = _COMMAND_CACHE.get(command_key)
            if cached and now - cached[0] <= COMMAND_RESULT_SECONDS:
                return _cache_copy(
                    cached[1], target=target, command_id=command_id, replay=True
                )
        cached = _ACTIVATION_CACHE.get(activation_key)
        if cached and now - cached[0] <= ACTIVATION_REPLAY_SECONDS:
            return _cache_copy(
                cached[1], target=target, command_id=command_id, replay=True
            )
    return None


def _store_activation_result(
    *,
    tool_name: str,
    activation_key: str,
    command_id: str | None,
    result: ToolResult,
) -> None:
    now = time.monotonic()
    with _CACHE_LOCK:
        _ACTIVATION_CACHE[activation_key] = (now, result.model_copy(deep=True))
        command_key = _command_key(tool_name, command_id)
        if command_key:
            _COMMAND_CACHE[command_key] = (now, result.model_copy(deep=True))


def _execute_registered_tool(
    tool_name: str,
    args: dict[str, Any],
    timeout_seconds: float,
) -> ToolResult:
    registry: dict[str, Callable[[], ToolResult | tuple[bool, str]]] = {
        "open_edge": lambda: open_edge(
            **{key: value for key, value in args.items() if not key.startswith("_")}
        ),
        "open_zoom": lambda: open_zoom(),
        "open_word": lambda: open_word(),
        "open_excel": lambda: open_excel(),
        "open_powerpoint": lambda: open_powerpoint(),
        "open_onenote": lambda: open_onenote(),
        "open_sample_document": lambda: open_sample_document(),
        "presentation_get_status": lambda: get_presentation_status(),
        "presentation_open_configured": lambda: open_configured_presentation_on_content_display(),
        "presentation_start_slideshow": lambda: start_configured_slideshow_on_content_display(),
        "presentation_next_slide": lambda: next_presentation_slide(),
        "presentation_previous_slide": lambda: previous_presentation_slide(),
        "presentation_go_to_slide": lambda: go_to_presentation_slide(
            int(args["slide_number"])
        ),
        "presentation_end_slideshow": lambda: end_configured_slideshow(),
        "presentation_close": lambda: close_powerpoint_discarding_changes(),
        "system_open_teams": lambda: open_managed_application_on_content_display("teams"),
        "system_close_teams": lambda: close_managed_application_from_desktop("teams"),
        "system_open_onenote": lambda: open_managed_application_on_content_display("onenote"),
        "system_close_onenote": lambda: close_managed_application_from_desktop("onenote"),
        "system_music_play_random": lambda: play_random_music_on_content_display(),
        "system_music_stop": lambda: stop_music_from_desktop(),
    }

    if tool_name not in registry:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Unknown tool: {tool_name}",
            data={
                "args": {
                    key: value for key, value in args.items() if not key.startswith("_")
                }
            },
        )

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(registry[tool_name])
    try:
        result = future.result(timeout=timeout_seconds)
        return _normalise_tool_result(tool_name, result, args, timeout_seconds)
    except TimeoutError:
        future.cancel()
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Tool timed out after {timeout_seconds:.1f} seconds.",
            data={
                "args": {
                    key: value for key, value in args.items() if not key.startswith("_")
                },
                "timeout_seconds": timeout_seconds,
                "timed_out": True,
            },
        )
    except Exception as exc:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Tool failed: {exc}",
            data={
                "args": {
                    key: value for key, value in args.items() if not key.startswith("_")
                },
                "timeout_seconds": timeout_seconds,
                "error": str(exc),
            },
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def run_tool(
    tool_name: str,
    args: dict,
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> ToolResult:
    args = dict(args or {})
    if tool_name.startswith("system_") and (
        "teams" in tool_name
        or "onenote" in tool_name
        or "music" in tool_name
    ):
        timeout_seconds = max(timeout_seconds, MANAGED_APPLICATION_TIMEOUT_SECONDS)
    if tool_name in {
        "presentation_open_configured",
        "presentation_start_slideshow",
        "presentation_close",
    }:
        timeout_seconds = max(timeout_seconds, POWERPOINT_DESKTOP_TIMEOUT_SECONDS)

    target = _ACTIVATION_TARGETS.get(tool_name)
    if target is None:
        return _execute_registered_tool(tool_name, args, timeout_seconds)

    command_id = str(args.get("_command_id") or "").strip() or None
    activation_key = _activation_key(tool_name, args)
    lock = _TARGET_LOCKS[target]

    # The lock is acquired before cache inspection so a concurrent duplicate waits
    # for the first immediate invocation and then receives its verified result.
    with lock:
        now = time.monotonic()
        _purge_cache(now)
        cached = _cached_result(
            tool_name=tool_name,
            activation_key=activation_key,
            command_id=command_id,
            target=target,
            now=now,
        )
        if cached is not None:
            return cached

        result = _execute_registered_tool(tool_name, args, timeout_seconds)
        annotated = _cache_copy(
            result,
            target=target,
            command_id=command_id,
            replay=False,
        )
        _store_activation_result(
            tool_name=tool_name,
            activation_key=activation_key,
            command_id=command_id,
            result=annotated,
        )
        return annotated
