from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket


class EventFactory:
    def __init__(self, source: str) -> None:
        self._source = source
        self._sequence = 0
        self._snapshot_revision = 0
        self._lock = threading.Lock()
        self.server_instance_id = f"boot_{uuid.uuid4().hex}"

    def build(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return {
            "protocol_version": "2.0",
            "server_instance_id": self.server_instance_id,
            "event_id": f"evt_{uuid.uuid4().hex}",
            "sequence": sequence,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "source": self._source,
            "type": event_type,
            "payload": payload or {},
        }

    def next_snapshot_revision(self) -> int:
        with self._lock:
            self._snapshot_revision += 1
            return self._snapshot_revision


@dataclass
class _ClientChannel:
    websocket: WebSocket
    queue: asyncio.Queue[dict[str, Any]]
    sender_task: asyncio.Task[None]


class WebSocketHub:
    """Nonblocking fan-out with one bounded sender queue per client."""

    def __init__(self, *, queue_size: int = 32, send_timeout_seconds: float = 1.5) -> None:
        self._clients: dict[WebSocket, _ClientChannel] = {}
        self._lock = asyncio.Lock()
        self._queue_size = queue_size
        self._send_timeout_seconds = send_timeout_seconds

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        sender_task = asyncio.create_task(self._sender_loop(websocket, queue))
        async with self._lock:
            self._clients[websocket] = _ClientChannel(websocket, queue, sender_task)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            channel = self._clients.pop(websocket, None)
        if channel is None:
            return
        current = asyncio.current_task()
        if channel.sender_task is not current:
            channel.sender_task.cancel()
            try:
                await channel.sender_task
            except asyncio.CancelledError:
                pass

    async def broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            channels = list(self._clients.values())
        for channel in channels:
            self._offer(channel.queue, event)

    async def send_to(self, websocket: WebSocket, event: dict[str, Any]) -> None:
        async with self._lock:
            channel = self._clients.get(websocket)
        if channel is not None:
            self._offer(channel.queue, event)

    def _offer(self, queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # A slow client may miss an intermediate snapshot, but it can never
            # block Vision, heartbeat, or another connected client.
            pass

    async def _sender_loop(
        self,
        websocket: WebSocket,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        try:
            while True:
                event = await queue.get()
                try:
                    await asyncio.wait_for(
                        websocket.send_json(event),
                        timeout=self._send_timeout_seconds,
                    )
                finally:
                    queue.task_done()
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            async with self._lock:
                self._clients.pop(websocket, None)
            try:
                await websocket.close()
            except Exception:
                pass
