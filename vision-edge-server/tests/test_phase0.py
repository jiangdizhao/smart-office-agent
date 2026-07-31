from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig, load_config
from app.events import EventFactory
from app.hardware import classify_camera_performance, select_best_camera_attempt
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
vision:
  enabled: false
  start_on_startup: false
""".strip(),
        encoding="utf-8",
    )
    config, resolved = load_config(config_file)
    assert resolved == config_file.resolve()
    assert config.service_name == "test-vision"
    assert config.server.port == 9123
    assert config.camera.enabled is False
    assert config.camera.device_index == 1
    assert config.camera.probe_modes[0].fourcc == "MJPG"


def test_camera_performance_classification() -> None:
    assert classify_camera_performance(12.0, ready_min_fps=10.0, degraded_min_fps=3.0) == "ready"
    assert classify_camera_performance(6.0, ready_min_fps=10.0, degraded_min_fps=3.0) == "degraded"
    assert classify_camera_performance(1.1, ready_min_fps=10.0, degraded_min_fps=3.0) == "unusable_for_realtime"


def test_camera_selection_prefers_realtime_then_resolution_match() -> None:
    attempts = [
        {
            "successful": True,
            "status": "ready",
            "requested": {"backend": "DSHOW", "fourcc": "MJPG", "fps": 30},
            "performance": {"measured_fps": 28.0, "resolution_match": False},
            "sample": {"success_ratio": 1.0, "mean_read_ms": 35.0},
        },
        {
            "successful": True,
            "status": "ready",
            "requested": {"backend": "DSHOW", "fourcc": "MJPG", "fps": 15},
            "performance": {"measured_fps": 14.0, "resolution_match": True},
            "sample": {"success_ratio": 1.0, "mean_read_ms": 70.0},
        },
        {
            "successful": True,
            "status": "degraded",
            "requested": {"backend": "DSHOW", "fourcc": "YUY2", "fps": 15},
            "performance": {"measured_fps": 5.0, "resolution_match": True},
            "sample": {"success_ratio": 1.0, "mean_read_ms": 200.0},
        },
    ]
    selected = select_best_camera_attempt(attempts)
    assert selected is not None
    assert selected["requested"]["fourcc"] == "MJPG"
    assert selected["requested"]["fps"] == 15


def test_event_sequence_is_monotonic_and_boot_scoped() -> None:
    factory = EventFactory("test")
    first = factory.build("one")
    second = factory.build("two")
    assert first["protocol_version"] == "2.0"
    assert first["server_instance_id"].startswith("boot_")
    assert first["server_instance_id"] == second["server_instance_id"]
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["event_id"] != second["event_id"]
    assert factory.next_snapshot_revision() == 1
    assert factory.next_snapshot_revision() == 2


def test_http_and_websocket_contract() -> None:
    config = AppConfig.model_validate(
        {
            "service_name": "rtx-vision-edge-server",
            "server": {"heartbeat_seconds": 60},
            "camera": {"enabled": False, "probe_on_startup": False},
            "gpu": {"probe_on_startup": False},
            "vision": {"enabled": False, "start_on_startup": False},
        }
    )
    app = create_app(config)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["server_instance_id"].startswith("boot_")
        assert health.json()["client_schema_version"] == "phase6.0"

        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["service"] == "rtx-vision-edge-server"

        client_state = client.get("/api/v1/client/state")
        assert client_state.status_code == 200
        state_payload = client_state.json()
        assert state_payload["schema_version"] == "phase6.0"
        assert state_payload["server_instance_id"].startswith("boot_")
        assert state_payload["snapshot_revision"] >= 1

        with client.websocket_connect("/ws/v1/events") as websocket:
            ready = websocket.receive_json()
            assert ready["type"] == "server_ready"
            assert ready["protocol_version"] == "2.0"
            assert ready["server_instance_id"].startswith("boot_")

            assert websocket.receive_json()["type"] == "state_snapshot"
            initial_client_state = websocket.receive_json()
            assert initial_client_state["type"] == "client_state_snapshot"
            assert initial_client_state["payload"]["schema_version"] == "phase6.0"
            assert initial_client_state["payload"]["snapshot_revision"] >= 1

            websocket.send_json({"type": "ping", "client_time": "test"})
            pong = websocket.receive_json()
            assert pong["type"] == "pong"
            assert pong["payload"]["client_time"] == "test"

            websocket.send_json({"type": "get_client_state"})
            refreshed = websocket.receive_json()
            assert refreshed["type"] == "client_state_snapshot"
            assert refreshed["payload"]["schema_version"] == "phase6.0"
            assert (
                refreshed["payload"]["snapshot_revision"]
                > initial_client_state["payload"]["snapshot_revision"]
            )
