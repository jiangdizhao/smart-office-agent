from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import AppConfig, load_config
from app.events import EventFactory, WebSocketHub
from app.logging_json import configure_logging
from app.runtime import VisionRuntime

logger = logging.getLogger(__name__)


def create_app(config: AppConfig | None = None) -> FastAPI:
    if config is None:
        loaded_config, config_path = load_config()
    else:
        loaded_config = config
        config_path = None

    configure_logging(loaded_config.server.log_level)
    event_factory = EventFactory(loaded_config.service_name)
    hub = WebSocketHub()
    runtime = VisionRuntime(loaded_config, event_factory, hub)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = loaded_config
        app.state.config_path = str(config_path) if config_path else None
        app.state.event_factory = event_factory
        app.state.hub = hub
        app.state.runtime = runtime
        tasks: list[asyncio.Task[Any]] = []

        logger.info(
            "vision_server_starting",
            extra={
                "event": "vision_server_starting",
                "service": loaded_config.service_name,
                "version": loaded_config.version,
                "phase": loaded_config.phase,
                "config_path": app.state.config_path,
            },
        )
        tasks.append(asyncio.create_task(_heartbeat_loop(runtime)))
        if loaded_config.gpu.probe_on_startup or (
            loaded_config.camera.enabled and loaded_config.camera.probe_on_startup
        ):
            tasks.append(
                asyncio.create_task(
                    runtime.run_hardware_probes(
                        include_gpu=loaded_config.gpu.probe_on_startup,
                        include_camera=(
                            loaded_config.camera.enabled and loaded_config.camera.probe_on_startup
                        ),
                    )
                )
            )

        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            logger.info("vision_server_stopped", extra={"event": "vision_server_stopped"})

    app = FastAPI(
        title="RTX Vision Edge Server",
        version=loaded_config.version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=loaded_config.server.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        ready, reasons = runtime.readiness()
        return {
            "status": "ok",
            "service": loaded_config.service_name,
            "version": loaded_config.version,
            "phase": loaded_config.phase,
            "ready": ready,
            "degraded_reasons": reasons,
        }

    @app.get("/api/v1/status", tags=["system"])
    async def status() -> dict[str, Any]:
        return runtime.public_status()

    @app.get("/api/v1/config/public", tags=["system"])
    async def public_config() -> dict[str, Any]:
        return {
            "service_name": loaded_config.service_name,
            "version": loaded_config.version,
            "phase": loaded_config.phase,
            "server": {
                "heartbeat_seconds": loaded_config.server.heartbeat_seconds,
            },
            "camera": loaded_config.camera.model_dump(exclude={"probe_on_startup"}),
            "gpu": loaded_config.gpu.model_dump(exclude={"probe_on_startup"}),
        }

    @app.post("/api/v1/probe", tags=["hardware"])
    async def rerun_probe() -> dict[str, Any]:
        if runtime.probe_running:
            raise HTTPException(status_code=409, detail="A hardware probe is already running")
        return await runtime.run_hardware_probes()

    @app.websocket("/ws/v1/events")
    async def event_stream(websocket: WebSocket) -> None:
        await hub.connect(websocket)
        logger.info(
            "websocket_connected",
            extra={"event": "websocket_connected", "client_count": hub.client_count},
        )
        try:
            await websocket.send_json(
                event_factory.build(
                    "server_ready",
                    {
                        "service": loaded_config.service_name,
                        "version": loaded_config.version,
                        "phase": loaded_config.phase,
                    },
                )
            )
            await websocket.send_json(event_factory.build("state_snapshot", runtime.public_status()))
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        event_factory.build("client_error", {"detail": "Expected a JSON object"})
                    )
                    continue

                message_type = str(message.get("type", "")).strip()
                if message_type == "ping":
                    await websocket.send_json(
                        event_factory.build("pong", {"client_time": message.get("client_time")})
                    )
                elif message_type == "get_state":
                    await websocket.send_json(
                        event_factory.build("state_snapshot", runtime.public_status())
                    )
                else:
                    await websocket.send_json(
                        event_factory.build(
                            "client_error",
                            {"detail": "Unsupported Phase 0 client message", "received_type": message_type},
                        )
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)
            logger.info(
                "websocket_disconnected",
                extra={"event": "websocket_disconnected", "client_count": hub.client_count},
            )

    return app


async def _heartbeat_loop(runtime: VisionRuntime) -> None:
    while True:
        await asyncio.sleep(runtime.config.server.heartbeat_seconds)
        await runtime.hub.broadcast(
            runtime.event_factory.build(
                "heartbeat",
                {
                    "uptime_seconds": runtime.uptime_seconds(),
                    "ready": runtime.readiness()[0],
                    "websocket_clients": runtime.hub.client_count,
                },
            )
        )


app = create_app()
