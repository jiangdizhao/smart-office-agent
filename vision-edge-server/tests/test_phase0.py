from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig, load_config
from app.events import EventFactory
from app.main import create_app


def test_load_config(tmp_path: Path) -> None:
    config_file = tmp_path / "vision.yaml"
    config_file.write_text(
        """
service_name: test-vision
server:
  port: 9123
camera:
  enabled: false
  probe_on_startup: false
gpu:
  probe_on_startup: false
""".strip(),
        encoding="utf-8",
    )
    config, resolved = load_config(config_file)
    assert resolved == config_file.resolve()
    assert config.service_name == "test-vision"
    assert config.server.port == 9123
    assert config.camera.enabled is False


def test_event_sequence_is_monotonic() -> None:
    factory = EventFactory("test")
    first = factory.build("one")
    second = factory.build("two")
    assert first["protocol_version"] == "1.0"
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["event_id"] != second["event_id"]


def test_http_and_websocket_contract() -> None:
    config = AppConfig.model_validate(
        {
            "service_name": "rtx-vision-edge-server",
            "server": {"heartbeat_seconds": 60},
            "camera": {"enabled": False, "probe_on_startup": False},
            "gpu": {"probe_on_startup": False},
        }
    )
    app = create_app(config)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["service"] == "rtx-vision-edge-server"

        with client.websocket_connect("/ws/v1/events") as websocket:
            assert websocket.receive_json()["type"] == "server_ready"
            assert websocket.receive_json()["type"] == "state_snapshot"
            websocket.send_json({"type": "ping", "client_time": "test"})
            pong = websocket.receive_json()
            assert pong["type"] == "pong"
            assert pong["payload"]["client_time"] == "test"
