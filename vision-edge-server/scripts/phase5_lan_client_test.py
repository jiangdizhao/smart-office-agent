from __future__ import annotations

import argparse
import asyncio
import json
import sys
from urllib.parse import urlparse, urlunparse

import httpx
import websockets

EXPECTED_CLIENT_SCHEMA = "phase5.2"


def websocket_url(http_base: str) -> str:
    parsed = urlparse(http_base.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/ws/v1/events", "", "", ""))


async def check_websocket(ws_url: str, timeout_seconds: float) -> dict:
    async with websockets.connect(ws_url, open_timeout=timeout_seconds) as socket:
        await socket.send(json.dumps({"type": "get_client_state"}))
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        seen: list[str] = []
        while asyncio.get_running_loop().time() < deadline:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
            event = json.loads(raw)
            event_type = str(event.get("type") or "")
            seen.append(event_type)
            if event_type != "client_state_snapshot":
                continue
            payload = event.get("payload") or {}
            if payload.get("schema_version") != EXPECTED_CLIENT_SCHEMA:
                raise RuntimeError(f"unexpected client schema: {payload.get('schema_version')}")
            return {"events_seen": seen, "client_state": payload}
    raise RuntimeError("client_state_snapshot was not received")


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Validate RTX Phase 5 HTTP and WebSocket access from the Smart Office LAN client."
    )
    parser.add_argument("--server", required=True, help="Example: http://192.168.1.50:8015")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()
    base = args.server.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
            health_response = await client.get(f"{base}/health")
            health_response.raise_for_status()
            state_response = await client.get(f"{base}/api/v1/client/state")
            state_response.raise_for_status()
            health = health_response.json()
            state = state_response.json()
        if state.get("schema_version") != EXPECTED_CLIENT_SCHEMA:
            raise RuntimeError(f"unexpected HTTP client schema: {state.get('schema_version')}")
        ws_result = await check_websocket(websocket_url(base), args.timeout_seconds)
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("PASS: Phase 5 RTX vision service is reachable over the LAN.")
    print(f"HTTP service: {health.get('service')} {health.get('version')} ({health.get('phase')})")
    print(f"Ready: {health.get('ready')} degraded={health.get('degraded_reasons')}")
    print(f"Client schema: {state.get('schema_version')}")
    print(f"Scene: {state.get('scene_state')} people={state.get('person_count')}")
    primary = state.get("primary") or {}
    print(
        "Primary: "
        f"track={primary.get('track_id')} session={primary.get('visitor_session_id')} "
        f"provisional={primary.get('provisional_session_id')} "
        f"stable={primary.get('session_stable')} returning={primary.get('returning_visitor')} "
        f"greeting={primary.get('greeting_kind')} eligible={primary.get('greeting_eligible')}"
    )
    print(f"WebSocket events: {ws_result['events_seen']}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
