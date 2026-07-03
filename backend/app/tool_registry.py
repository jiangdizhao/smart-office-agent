from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any

from app.models import ToolResult
from app.tools.windows_controller import (
    open_edge,
    open_zoom,
    open_word,
    open_excel,
    open_powerpoint,
    open_onenote,
    open_sample_document,
)


DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0


def _normalize_tool_result(
    tool_name: str,
    result: ToolResult | tuple[bool, str],
    args: dict[str, Any],
    timeout_seconds: float,
) -> ToolResult:
    if isinstance(result, ToolResult):
        result.data.update(
            {
                "args": args,
                "timeout_seconds": timeout_seconds,
            }
        )
        return result

    ok, message = result
    return ToolResult(
        tool_name=tool_name,
        ok=ok,
        message=message,
        data={
            "args": args,
            "timeout_seconds": timeout_seconds,
        },
    )


def run_tool(
    tool_name: str,
    args: dict,
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> ToolResult:
    registry: dict[str, Callable[[], ToolResult | tuple[bool, str]]] = {
        "open_edge": lambda: open_edge(**args),
        "open_zoom": lambda: open_zoom(),
        "open_word": lambda: open_word(),
        "open_excel": lambda: open_excel(),
        "open_powerpoint": lambda: open_powerpoint(),
        "open_onenote": lambda: open_onenote(),
        "open_sample_document": lambda: open_sample_document(),
    }

    if tool_name not in registry:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Unknown tool: {tool_name}",
            data={"args": args},
        )

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(registry[tool_name])
    try:
        result = future.result(timeout=timeout_seconds)
        return _normalize_tool_result(tool_name, result, args, timeout_seconds)
    except TimeoutError:
        future.cancel()
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"Tool timed out after {timeout_seconds:.1f} seconds.",
            data={
                "args": args,
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
                "args": args,
                "timeout_seconds": timeout_seconds,
                "error": str(exc),
            },
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
