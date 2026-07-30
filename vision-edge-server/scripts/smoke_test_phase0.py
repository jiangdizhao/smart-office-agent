from __future__ import annotations

import argparse
import asyncio
import json
from urllib.parse import urlparse

import httpx
import websockets


def websocket_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws/v1/events"


async def run(base_url: str, timeout: float) -> None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        health = (await client.get(f"{base_url}/health")).json()
        status = (await client.get(f"{base_url}/api/v1/status")).json()

    assert health["status"] == "ok", health
    assert str(health.get("phase", "")).startswith("phase"), health
    assert status["service"] == "rtx-vision-edge-server", status

    received_types: list[str] = []
    async with websockets.connect(websocket_url(base_url), open_timeout=timeout) as websocket:
        for _ in range(2):
            event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
            received_types.append(event["type"])
        await websocket.send(json.dumps({"type": "ping", "client_time": "transport-smoke"}))
        while True:
            event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
            received_types.append(event["type"])
            if event["type"] == "pong":
                break

    assert received_types[:2] == ["server_ready", "state_snapshot"], received_types
    assert "pong" in received_types, received_types
    print("PASS: health, status, WebSocket snapshot, and ping/pong contract are available.")
    print(json.dumps({"health": health, "status": status, "events": received_types}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    asyncio.run(run(args.base_url.rstrip("/"), args.timeout))


if __name__ == "__main__":
    main()
