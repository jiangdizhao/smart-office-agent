from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket


class EventFactory:
    def __init__(self, source: str) -> None:
        self._source = source
        self._sequence = 0
        self._lock = threading.Lock()

    def build(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return {
            "protocol_version": "1.0",
            "event_id": f"evt_{uuid.uuid4().hex}",
            "sequence": sequence,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "source": self._source,
            "type": event_type,
            "payload": payload or {},
        }


class WebSocketHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        stale: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(event)
            except Exception:
                stale.append(client)
        if stale:
            async with self._lock:
                for client in stale:
                    self._clients.discard(client)
