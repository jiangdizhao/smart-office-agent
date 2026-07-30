from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config import AppConfig
from app.events import EventFactory, WebSocketHub
from app.hardware import probe_camera, probe_gpu

logger = logging.getLogger(__name__)


class VisionRuntime:
    def __init__(self, config: AppConfig, event_factory: EventFactory, hub: WebSocketHub) -> None:
        self.config = config
        self.event_factory = event_factory
        self.hub = hub
        self.started_monotonic = time.monotonic()
        self.gpu: dict[str, Any] | None = None
        self.camera: dict[str, Any] | None = None
        self.probe_running = False
        self._probe_lock = asyncio.Lock()

    def uptime_seconds(self) -> float:
        return round(time.monotonic() - self.started_monotonic, 3)

    def readiness(self) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if self.config.gpu.require_cuda and not bool((self.gpu or {}).get("ok")):
            reasons.append("required CUDA provider is not ready")
        if self.config.camera.enabled and self.camera is not None and not self.camera.get("ok"):
            reasons.append("camera probe did not produce a frame")
        return not reasons, reasons

    def public_status(self) -> dict[str, Any]:
        ready, reasons = self.readiness()
        return {
            "service": self.config.service_name,
            "version": self.config.version,
            "phase": self.config.phase,
            "ready": ready,
            "degraded_reasons": reasons,
            "uptime_seconds": self.uptime_seconds(),
            "probe_running": self.probe_running,
            "websocket_clients": self.hub.client_count,
            "gpu": self.gpu,
            "camera": self.camera,
        }

    async def run_hardware_probes(self) -> dict[str, Any]:
        async with self._probe_lock:
            self.probe_running = True
            logger.info("hardware_probe_started", extra={"event": "hardware_probe_started"})
            try:
                gpu_task = asyncio.to_thread(probe_gpu, self.config.gpu)
                if self.config.camera.enabled:
                    camera_task = asyncio.to_thread(probe_camera, self.config.camera)
                    self.gpu, self.camera = await asyncio.gather(gpu_task, camera_task)
                else:
                    self.gpu = await gpu_task
                    self.camera = probe_camera(self.config.camera)
                payload = {
                    "gpu_ok": bool((self.gpu or {}).get("ok")),
                    "camera_ok": bool((self.camera or {}).get("ok")),
                    "status": self.public_status(),
                }
                event = self.event_factory.build("probe_completed", payload)
                await self.hub.broadcast(event)
                logger.info(
                    "hardware_probe_completed",
                    extra={
                        "event": "hardware_probe_completed",
                        "gpu_ok": payload["gpu_ok"],
                        "camera_ok": payload["camera_ok"],
                    },
                )
                return payload
            finally:
                self.probe_running = False
