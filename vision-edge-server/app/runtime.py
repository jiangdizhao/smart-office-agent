from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from app.camera_selection import select_startup_camera
from app.config import AppConfig
from app.events import EventFactory, WebSocketHub
from app.gpu_probe import probe_gpu
from app.hardware import probe_camera
from app.vision_pipeline import VisionPipeline

logger = logging.getLogger(__name__)


class VisionRuntime:
    def __init__(self, config: AppConfig, event_factory: EventFactory, hub: WebSocketHub) -> None:
        self.config = config
        self.event_factory = event_factory
        self.hub = hub
        self.started_monotonic = time.monotonic()
        self.gpu: dict[str, Any] | None = None
        self.camera: dict[str, Any] | None = None
        self.camera_selection: dict[str, Any] | None = None
        self.probe_running = False
        self._probe_lock = asyncio.Lock()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._camera_selection_done = False
        self._vision_stale = False
        self._last_watchdog_monotonic = 0.0
        server_root = Path(__file__).resolve().parents[1]
        self.vision = VisionPipeline(config, server_root, self._emit_from_thread)

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop

    def _emit_from_thread(self, event_type: str, payload: dict[str, Any]) -> None:
        loop = self._event_loop
        if loop is None or loop.is_closed():
            return
        event = self.event_factory.build(event_type, payload)
        future = asyncio.run_coroutine_threadsafe(self.hub.broadcast(event), loop)
        future.add_done_callback(self._consume_broadcast_result)

    @staticmethod
    def _consume_broadcast_result(future: Any) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("vision_event_broadcast_failed")

    def uptime_seconds(self) -> float:
        return round(time.monotonic() - self.started_monotonic, 3)

    async def _select_camera_once(self) -> None:
        if self._camera_selection_done or not self.config.camera.enabled:
            return
        self.camera_selection = await asyncio.to_thread(
            select_startup_camera, self.config.camera
        )
        self._camera_selection_done = True

    async def start_vision(self) -> dict[str, Any]:
        await self._select_camera_once()
        await asyncio.to_thread(self.vision.start)
        return self.vision.status()

    async def stop_vision(self) -> dict[str, Any]:
        await asyncio.to_thread(self.vision.stop)
        return self.vision.status()

    async def restart_vision(self) -> dict[str, Any]:
        await asyncio.to_thread(self.vision.stop)
        self._camera_selection_done = False
        await self._select_camera_once()
        await asyncio.to_thread(self.vision.start)
        return self.vision.status()

    async def visit_watchdog_tick(self) -> None:
        """Expire Visits and detect frozen camera/pipeline state without new frames."""
        now = time.monotonic()
        self._last_watchdog_monotonic = now
        for event_type, payload in self.vision.visitor_sessions.expire_due(now):
            await self.hub.broadcast(self.event_factory.build(event_type, payload))

        camera_status = self.vision.camera.status()
        frame_age_ms = camera_status.get("frame_age_ms")
        stale_threshold_ms = max(
            2_500.0,
            4_000.0 / max(float(self.config.detection.inference_hz), 1.0),
        )
        stale = bool(
            self.vision.running
            and (
                frame_age_ms is None
                or float(frame_age_ms) > stale_threshold_ms
                or not bool(camera_status.get("open"))
            )
        )
        if stale == self._vision_stale:
            return
        self._vision_stale = stale
        event_type = "vision_stale" if stale else "vision_recovered"
        await self.hub.broadcast(
            self.event_factory.build(
                event_type,
                {
                    "frame_age_ms": frame_age_ms,
                    "stale_threshold_ms": stale_threshold_ms,
                    "camera_open": bool(camera_status.get("open")),
                    "camera_running": bool(camera_status.get("running")),
                },
            )
        )

    def readiness(self) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if self.config.gpu.require_cuda and not bool((self.gpu or {}).get("ok")):
            reasons.append("required CUDA provider is not ready")
        if self.config.vision.enabled:
            vision_status = self.vision.status()
            if vision_status.get("status") != "ready":
                reasons.append(
                    f"vision pipeline is not ready (status={vision_status.get('status')})"
                )
            if self._vision_stale:
                reasons.append("vision frames are stale")
        elif self.config.camera.enabled and self.camera is not None and not self.camera.get("ok"):
            camera_status = str(self.camera.get("status") or "unknown")
            reasons.append(f"camera is not realtime-ready (status={camera_status})")
        return not reasons, reasons

    def public_status(self) -> dict[str, Any]:
        ready, reasons = self.readiness()
        return {
            "service": self.config.service_name,
            "version": self.config.version,
            "phase": self.config.phase,
            "server_instance_id": self.event_factory.server_instance_id,
            "ready": ready,
            "degraded_reasons": reasons,
            "uptime_seconds": self.uptime_seconds(),
            "probe_running": self.probe_running,
            "websocket_clients": self.hub.client_count,
            "vision_stale": self._vision_stale,
            "watchdog_age_ms": round(
                max(time.monotonic() - self._last_watchdog_monotonic, 0.0) * 1000.0,
                3,
            ) if self._last_watchdog_monotonic else None,
            "gpu": self.gpu,
            "camera_selection": self.camera_selection,
            "camera_probe": self.camera,
            "vision": self.vision.status(),
        }

    async def run_hardware_probes(
        self,
        *,
        include_gpu: bool = True,
        include_camera: bool = True,
    ) -> dict[str, Any]:
        if include_camera and self.vision.running:
            raise RuntimeError("Stop the vision pipeline before running a camera hardware probe")
        async with self._probe_lock:
            self.probe_running = True
            logger.info(
                "hardware_probe_started",
                extra={
                    "event": "hardware_probe_started",
                    "include_gpu": include_gpu,
                    "include_camera": include_camera,
                },
            )
            try:
                jobs: dict[str, Any] = {}
                if include_gpu:
                    jobs["gpu"] = asyncio.to_thread(probe_gpu, self.config.gpu)
                if include_camera:
                    jobs["camera"] = asyncio.to_thread(probe_camera, self.config.camera)
                if jobs:
                    names = list(jobs)
                    results = await asyncio.gather(*(jobs[name] for name in names))
                    for name, result in zip(names, results, strict=True):
                        if name == "gpu":
                            self.gpu = result
                        else:
                            self.camera = result
            finally:
                self.probe_running = False
            payload = {
                "gpu_ok": None if self.gpu is None else bool(self.gpu.get("ok")),
                "camera_ok": None if self.camera is None else bool(self.camera.get("ok")),
                "status": self.public_status(),
            }
            await self.hub.broadcast(self.event_factory.build("probe_completed", payload))
            return payload
