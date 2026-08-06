from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

_CLIENT: httpx.AsyncClient | None = None
_CLIENT_LOCK = asyncio.Lock()


def _integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


async def shared_openai_http_client() -> httpx.AsyncClient:
    """Return one process-local keep-alive client for OpenAI HTTP requests.

    Request-specific deadlines are still supplied by each caller. The shared
    transport only owns DNS/TCP/TLS reuse and bounded connection concurrency.
    """

    global _CLIENT
    client = _CLIENT
    if client is not None and not client.is_closed:
        return client

    async with _CLIENT_LOCK:
        client = _CLIENT
        if client is None or client.is_closed:
            maximum_connections = _integer_env(
                "OPENAI_HTTP_MAX_CONNECTIONS", 20, 2, 100
            )
            maximum_keepalive = _integer_env(
                "OPENAI_HTTP_MAX_KEEPALIVE_CONNECTIONS", 10, 1, maximum_connections
            )
            keepalive_expiry = float(
                _integer_env("OPENAI_HTTP_KEEPALIVE_SECONDS", 45, 5, 300)
            )
            _CLIENT = httpx.AsyncClient(
                timeout=None,
                limits=httpx.Limits(
                    max_connections=maximum_connections,
                    max_keepalive_connections=maximum_keepalive,
                    keepalive_expiry=keepalive_expiry,
                ),
                follow_redirects=True,
            )
        return _CLIENT


async def close_openai_http_client() -> None:
    global _CLIENT
    async with _CLIENT_LOCK:
        client = _CLIENT
        _CLIENT = None
    if client is not None and not client.is_closed:
        await client.aclose()


def openai_http_pool_status() -> dict[str, Any]:
    client = _CLIENT
    return {
        "configured": client is not None,
        "closed": bool(client.is_closed) if client is not None else None,
        "max_connections": _integer_env("OPENAI_HTTP_MAX_CONNECTIONS", 20, 2, 100),
        "max_keepalive_connections": _integer_env(
            "OPENAI_HTTP_MAX_KEEPALIVE_CONNECTIONS", 10, 1, 100
        ),
        "keepalive_seconds": _integer_env("OPENAI_HTTP_KEEPALIVE_SECONDS", 45, 5, 300),
    }
