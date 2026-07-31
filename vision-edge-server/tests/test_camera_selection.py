from __future__ import annotations

from app.camera_selection import select_startup_camera
from app.config import CameraSettings


def _probe_result(index: int) -> dict:
    resolutions = {
        0: (1280, 720, 30.0),
        1: (1920, 1080, 30.0),
        2: (3840, 2160, 15.0),
    }
    if index not in resolutions:
        return {
            "ok": False,
            "status": "unavailable",
            "device_index": index,
            "error": "open failed",
        }
    width, height, fps = resolutions[index]
    return {
        "ok": True,
        "status": "ready",
        "device_index": index,
        "actual": {"width": width, "height": height, "backend": "DSHOW"},
        "selected_mode": {
            "backend": "DSHOW",
            "fourcc": "MJPG",
            "width": width,
            "height": height,
            "fps": fps,
        },
        "sample": {"success_ratio": 1.0},
        "performance": {"measured_fps": fps},
    }


def test_selects_camera_with_highest_actual_resolution(monkeypatch) -> None:
    monkeypatch.delenv("VISION_CAMERA_DEVICE_INDEX", raising=False)
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "3")
    monkeypatch.setattr(
        "app.camera_selection.probe_camera",
        lambda settings: _probe_result(settings.device_index),
    )
    settings = CameraSettings(device_index=0)

    result = select_startup_camera(settings)

    assert result["selected"] is True
    assert result["device_index"] == 2
    assert settings.device_index == 2
    assert settings.runtime_backend == "DSHOW"
    assert settings.runtime_fourcc == "MJPG"
    assert settings.runtime_fps == 15.0


def test_manual_override_bypasses_enumeration(monkeypatch) -> None:
    monkeypatch.setenv("VISION_CAMERA_DEVICE_INDEX", "4")
    called = False

    def fail_if_called(settings):
        nonlocal called
        called = True
        raise AssertionError("probe_camera must not run for a manual override")

    monkeypatch.setattr("app.camera_selection.probe_camera", fail_if_called)
    settings = CameraSettings(device_index=1)

    result = select_startup_camera(settings)

    assert called is False
    assert result["mode"] == "manual_override"
    assert settings.device_index == 4


def test_falls_back_to_configured_index_when_no_camera_is_usable(monkeypatch) -> None:
    monkeypatch.delenv("VISION_CAMERA_DEVICE_INDEX", raising=False)
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "2")
    monkeypatch.setattr(
        "app.camera_selection.probe_camera",
        lambda settings: {
            "ok": False,
            "status": "unavailable",
            "device_index": settings.device_index,
            "error": "open failed",
        },
    )
    settings = CameraSettings(device_index=1)

    result = select_startup_camera(settings)

    assert result["selected"] is False
    assert result["device_index"] == 1
    assert settings.device_index == 1
