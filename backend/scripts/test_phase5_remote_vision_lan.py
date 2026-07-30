from __future__ import annotations

import argparse
import asyncio
import json
import sys
from urllib.parse import urlparse, urlunparse

import httpx
import websockets


def to_ws_url(http_base: str) -> str:
    parsed = urlparse(http_base.rstrip("/"))
    return urlunparse(
        (
            "wss" if parsed.scheme == "https" else "ws",
            parsed.netloc,
            "/ws/v1/events",
            "",
            "",
            "",
        )
    )


async def receive_client_state(ws_url: str, timeout_seconds: float) -> dict:
    async with websockets.connect(ws_url, open_timeout=timeout_seconds) as socket:
        await socket.send(json.dumps({"type": "get_client_state"}))
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=timeout_seconds)
            event = json.loads(raw)
            if event.get("type") == "client_state_snapshot":
                return event.get("payload") or {}


async def run(server: str, timeout_seconds: float) -> int:
    base = server.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            health_response = await client.get(f"{base}/health")
            health_response.raise_for_status()
            state_response = await client.get(f"{base}/api/v1/client/state")
            state_response.raise_for_status()
            health = health_response.json()
            http_state = state_response.json()
        ws_state = await receive_client_state(to_ws_url(base), timeout_seconds)
        for label, state in (("HTTP", http_state), ("WebSocket", ws_state)):
            if state.get("schema_version") != "phase5.1":
                raise RuntimeError(
                    f"{label} returned schema {state.get('schema_version')!r}, expected 'phase5.1'"
                )
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("PASS: i5 client can reach the RTX Phase 5 vision service over HTTP and WebSocket.")
    print(
        f"Server: {health.get('service')} {health.get('version')} "
        f"phase={health.get('phase')} ready={health.get('ready')}"
    )
    print(
        f"Scene: {ws_state.get('scene_state')} people={ws_state.get('person_count')} "
        f"primary_track={ws_state.get('primary_track_id')}"
    )
    primary = ws_state.get("primary") or {}
    print(
        f"Primary session={primary.get('visitor_session_id')} "
        f"engaged={primary.get('engaged')} face={(primary.get('face') or {}).get('detected')} "
        f"greeting_eligible={primary.get('greeting_eligible')}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run this on the i5 Smart Office client to validate the RTX vision LAN link."
    )
    parser.add_argument("--server", required=True, help="Example: http://192.168.1.50:8015")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()
    return asyncio.run(run(args.server, args.timeout_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
