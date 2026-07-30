from __future__ import annotations

import argparse
import asyncio
import json
import time
from urllib.parse import urlparse

import websockets


def websocket_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws/v1/events"


async def watch(base_url: str, duration_seconds: float) -> None:
    url = websocket_url(base_url.rstrip("/"))
    deadline = None if duration_seconds <= 0 else time.monotonic() + duration_seconds
    print(f"Connecting to {url}")
    async with websockets.connect(url, open_timeout=10.0, ping_interval=20.0) as websocket:
        print("Connected. Press Ctrl+C to stop.")
        while True:
            if deadline is None:
                raw = await websocket.recv()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                print(raw)
                continue
            print(json.dumps(event, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Print Vision Edge WebSocket events.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0.0,
        help="Stop after this many seconds; zero means run until Ctrl+C.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(watch(args.base_url, args.duration_seconds))
    except KeyboardInterrupt:
        print("Event watcher stopped.")
    except asyncio.TimeoutError:
        print("Event watcher duration completed.")


if __name__ == "__main__":
    main()
