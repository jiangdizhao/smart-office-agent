from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import AppConfig, load_config
from app.events import EventFactory, WebSocketHub
from app.logging_json import configure_logging
from app.runtime import VisionRuntime


class IdentityEnrollmentRequest(BaseModel):
    track_id: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=120)
    consent: bool
    external_id: str | None = Field(default=None, max_length=200)
    identity_id: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


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
        runtime.bind_event_loop(asyncio.get_running_loop())
        tasks: list[asyncio.Task[Any]] = [asyncio.create_task(_heartbeat_loop(runtime))]
        if loaded_config.gpu.probe_on_startup:
            tasks.append(
                asyncio.create_task(runtime.run_hardware_probes(include_gpu=True, include_camera=False))
            )
        if loaded_config.vision.enabled and loaded_config.vision.start_on_startup:
            await runtime.start_vision()
        elif loaded_config.camera.enabled and loaded_config.camera.probe_on_startup:
            tasks.append(
                asyncio.create_task(runtime.run_hardware_probes(include_gpu=False, include_camera=True))
            )
        try:
            yield
        finally:
            await runtime.stop_vision()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="RTX Vision Edge Server",
        version=loaded_config.version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=loaded_config.server.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
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
        return loaded_config.model_dump(
            exclude={"camera": {"probe_on_startup"}, "gpu": {"probe_on_startup"}}
        )

    @app.post("/api/v1/probe", tags=["hardware"])
    async def rerun_probe(
        include_gpu: bool = Query(default=True),
        include_camera: bool = Query(default=True),
    ) -> dict[str, Any]:
        if runtime.probe_running:
            raise HTTPException(status_code=409, detail="A hardware probe is already running")
        try:
            return await runtime.run_hardware_probes(
                include_gpu=include_gpu,
                include_camera=include_camera,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/vision/start", tags=["vision"])
    async def start_vision() -> dict[str, Any]:
        return await runtime.start_vision()

    @app.post("/api/v1/vision/stop", tags=["vision"])
    async def stop_vision() -> dict[str, Any]:
        return await runtime.stop_vision()

    @app.post("/api/v1/vision/restart", tags=["vision"])
    async def restart_vision() -> dict[str, Any]:
        return await runtime.restart_vision()

    @app.get("/api/v1/detections", tags=["vision"])
    async def detections() -> dict[str, Any]:
        return runtime.vision.detections_snapshot()

    @app.get("/api/v1/tracks", tags=["vision"])
    async def tracks() -> dict[str, Any]:
        return runtime.vision.tracks_snapshot()

    @app.get("/api/v1/faces", tags=["face"])
    async def faces() -> dict[str, Any]:
        return runtime.vision.faces_snapshot()

    @app.get("/api/v1/identities", tags=["identity"])
    async def identities() -> dict[str, Any]:
        return runtime.vision.identities_snapshot()

    @app.post("/api/v1/identities/enroll", tags=["identity"])
    async def enroll_identity(request: IdentityEnrollmentRequest) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                runtime.vision.enroll_identity,
                track_id=request.track_id,
                display_name=request.display_name,
                consent=request.consent,
                external_id=request.external_id,
                metadata=request.metadata,
                identity_id=request.identity_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.delete("/api/v1/identities/{identity_id}", tags=["identity"])
    async def delete_identity(identity_id: str) -> dict[str, Any]:
        deleted = await asyncio.to_thread(runtime.vision.delete_identity, identity_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Identity was not found")
        return {"deleted": True, "identity_id": identity_id}

    @app.get("/api/v1/debug/frame.jpg", tags=["debug"])
    async def debug_frame() -> Response:
        data = runtime.vision.latest_preview()
        if data is None:
            raise HTTPException(status_code=503, detail="No debug frame is available yet")
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/debug/stream.mjpg", tags=["debug"])
    async def debug_stream() -> StreamingResponse:
        async def generate():
            delay = 1.0 / loaded_config.debug.preview_fps
            while True:
                data = runtime.vision.latest_preview()
                if data is not None:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
                await asyncio.sleep(delay)

        return StreamingResponse(
            generate(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.websocket("/ws/v1/events")
    async def event_stream(websocket: WebSocket) -> None:
        await hub.connect(websocket)
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
            await websocket.send_json(
                event_factory.build("state_snapshot", runtime.public_status())
            )
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
                            {
                                "detail": "Unsupported client message",
                                "received_type": message_type,
                            },
                        )
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

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
                    "vision": runtime.vision.status(),
                },
            )
        )


app = create_app()
