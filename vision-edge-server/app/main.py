from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.client_protocol import CLIENT_SCHEMA_VERSION, build_client_state
from app.config import AppConfig
from app.events import EventFactory, WebSocketHub
from app.logging_json import configure_logging
from app.runtime import VisionRuntime
from app.short_visit_config import load_config


class IdentityEnrollmentRequest(BaseModel):
    track_id: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=120)
    consent: bool
    external_id: str | None = Field(default=None, max_length=200)
    identity_id: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _require_local_identity_operator(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=403,
            detail="Identity enrollment and deletion are restricted to the local RTX operator.",
        )


def _client_state(runtime: VisionRuntime, event_factory: EventFactory) -> dict[str, Any]:
    ready, reasons = runtime.readiness()
    payload = build_client_state(
        service=runtime.config.service_name,
        version=runtime.config.version,
        phase=runtime.config.phase,
        ready=ready,
        degraded_reasons=reasons,
        tracks_snapshot=runtime.vision.tracks_snapshot(),
        uptime_seconds=runtime.uptime_seconds(),
    )
    camera_status = runtime.vision.camera.status()
    payload.update(
        {
            "server_instance_id": event_factory.server_instance_id,
            "snapshot_revision": event_factory.next_snapshot_revision(),
            "frame_age_ms": camera_status.get("frame_age_ms"),
            "vision_stale": bool(runtime.public_status().get("vision_stale")),
        }
    )
    return payload


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
        tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(_heartbeat_loop(runtime)),
            asyncio.create_task(_visit_watchdog_loop(runtime)),
        ]
        if loaded_config.gpu.probe_on_startup:
            tasks.append(
                asyncio.create_task(
                    runtime.run_hardware_probes(include_gpu=True, include_camera=False)
                )
            )
        if loaded_config.vision.enabled and loaded_config.vision.start_on_startup:
            await runtime.start_vision()
        elif loaded_config.camera.enabled and loaded_config.camera.probe_on_startup:
            tasks.append(
                asyncio.create_task(
                    runtime.run_hardware_probes(include_gpu=False, include_camera=True)
                )
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
            "server_instance_id": event_factory.server_instance_id,
            "client_schema_version": CLIENT_SCHEMA_VERSION,
            "ready": ready,
            "degraded_reasons": reasons,
        }

    @app.get("/api/v1/status", tags=["system"])
    async def status() -> dict[str, Any]:
        return runtime.public_status()

    @app.get("/api/v1/client/state", tags=["client"])
    async def client_state() -> dict[str, Any]:
        return _client_state(runtime, event_factory)

    @app.get("/api/v1/config/public", tags=["system"])
    async def public_config() -> dict[str, Any]:
        return loaded_config.model_dump(
            exclude={
                "camera": {"probe_on_startup"},
                "gpu": {"probe_on_startup"},
            }
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
    async def enroll_identity(
        request_body: IdentityEnrollmentRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_local_identity_operator(request)
        try:
            return await asyncio.to_thread(
                runtime.vision.enroll_identity,
                track_id=request_body.track_id,
                display_name=request_body.display_name,
                consent=request_body.consent,
                external_id=request_body.external_id,
                metadata=request_body.metadata,
                identity_id=request_body.identity_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.delete("/api/v1/identities/{identity_id}", tags=["identity"])
    async def delete_identity(identity_id: str, request: Request) -> dict[str, Any]:
        _require_local_identity_operator(request)
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
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                        + data
                        + b"\r\n"
                    )
                await asyncio.sleep(delay)

        return StreamingResponse(
            generate(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.websocket("/ws/v1/events")
    async def event_stream(websocket: WebSocket) -> None:
        await hub.connect(websocket)
        await hub.send_to(
            websocket,
            event_factory.build(
                "server_ready",
                {
                    "service": loaded_config.service_name,
                    "version": loaded_config.version,
                    "phase": loaded_config.phase,
                    "client_schema_version": CLIENT_SCHEMA_VERSION,
                    "server_instance_id": event_factory.server_instance_id,
                },
            ),
        )
        await hub.send_to(
            websocket,
            event_factory.build("state_snapshot", runtime.public_status()),
        )
        await hub.send_to(
            websocket,
            event_factory.build(
                "client_state_snapshot",
                _client_state(runtime, event_factory),
            ),
        )
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await hub.send_to(
                        websocket,
                        event_factory.build(
                            "client_error", {"detail": "Expected a JSON object"}
                        ),
                    )
                    continue
                message_type = str(message.get("type", "")).strip()
                if message_type == "ping":
                    await hub.send_to(
                        websocket,
                        event_factory.build(
                            "pong", {"client_time": message.get("client_time")}
                        ),
                    )
                elif message_type == "get_state":
                    await hub.send_to(
                        websocket,
                        event_factory.build("state_snapshot", runtime.public_status()),
                    )
                elif message_type == "get_client_state":
                    await hub.send_to(
                        websocket,
                        event_factory.build(
                            "client_state_snapshot",
                            _client_state(runtime, event_factory),
                        ),
                    )
                else:
                    await hub.send_to(
                        websocket,
                        event_factory.build(
                            "client_error",
                            {
                                "detail": "Unsupported client message",
                                "received_type": message_type,
                            },
                        ),
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
                    "client_schema_version": CLIENT_SCHEMA_VERSION,
                    "server_instance_id": runtime.event_factory.server_instance_id,
                },
            )
        )


async def _visit_watchdog_loop(runtime: VisionRuntime) -> None:
    while True:
        await asyncio.sleep(0.1)
        await runtime.visit_watchdog_tick()


app = create_app()
