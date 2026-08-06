from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Awaitable[None]]], Awaitable[None]]


def exhibition_admin_enabled() -> bool:
    value = os.getenv("SMART_OFFICE_EXHIBITION_ADMIN_MODE", "true").strip().casefold()
    return value not in {"0", "false", "off", "no"}


def _normalise_actor_payload(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, dict):
        return value, False
    payload = dict(value)
    changed = False

    if "actor_type" in payload and payload.get("actor_type") != "operator":
        payload["actor_type"] = "operator"
        changed = True

    actor_context = payload.get("actor_context")
    if isinstance(actor_context, dict):
        normalized_context = dict(actor_context)
        if normalized_context.get("type") != "operator":
            normalized_context["type"] = "operator"
            changed = True
        normalized_context["exhibition_admin_mode"] = True
        payload["actor_context"] = normalized_context

    # Some compatibility/task endpoints use a direct actor field.
    if "actor" in payload and payload.get("actor") != "operator":
        payload["actor"] = "operator"
        changed = True

    return payload, changed


class ExhibitionAdminModeMiddleware:
    """Normalize all exhibition requests to Operator before FastAPI validation.

    The exhibition is a function demonstration, not an authorization demo. Office
    execution therefore must not depend on a client remembering to send an employee
    role. Independent confirmation gates for draft creation, real email sending and
    destructive operations remain in their tool/task workflows.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[dict[str, Any]]], send: Callable[..., Awaitable[None]]) -> None:
        if (
            not exhibition_admin_enabled()
            or scope.get("type") != "http"
            or str(scope.get("method") or "").upper() not in {"POST", "PUT", "PATCH"}
        ):
            await self.app(scope, receive, send)
            return

        headers = {
            bytes(key).lower(): bytes(value)
            for key, value in scope.get("headers", [])
        }
        content_type = headers.get(b"content-type", b"").decode("latin-1").casefold()
        if "application/json" not in content_type:
            await self.app(scope, receive, send)
            return

        body_parts: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") == "http.disconnect":
                await self.app(scope, lambda: _return_message(message), send)
                return
            body_parts.append(bytes(message.get("body", b"")))
            more_body = bool(message.get("more_body", False))

        original_body = b"".join(body_parts)
        rewritten_body = original_body
        changed = False
        try:
            parsed = json.loads(original_body.decode("utf-8")) if original_body else None
            normalized, changed = _normalise_actor_payload(parsed)
            if changed:
                rewritten_body = json.dumps(
                    normalized,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
        except (UnicodeDecodeError, ValueError, TypeError):
            changed = False

        delivered = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {
                "type": "http.request",
                "body": rewritten_body,
                "more_body": False,
            }

        async def marked_send(message: dict[str, Any]) -> None:
            if changed and message.get("type") == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append(
                    (b"x-smart-office-exhibition-actor", b"operator")
                )
                message = {**message, "headers": response_headers}
            await send(message)

        await self.app(scope, replay_receive, marked_send)


async def _return_message(message: dict[str, Any]) -> dict[str, Any]:
    return message
