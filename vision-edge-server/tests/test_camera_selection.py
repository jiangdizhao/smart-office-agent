from __future__ import annotations

import pytest

from app.camera_selection import RequiredCameraUnavailableError, select_startup_camera
from app.config import CameraSettings


def test_selects_first_actual_4k_camera(monkeypatch) -> None:
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "4")
    attempts: list[int] = []

    def fake_probe(cv2, settings, device_index):
        attempts.append(device_index)
        resolutions = {
            0: (1280, 720),
            1: (1920, 1080),
            2: (3840, 2160),
            3: (3840, 2160),
        }
        width, height = resolutions.get(device_index, (0, 0))
        return {
            "device_index": device_index,
            "opened": width > 0,
            "frame_read": width > 0,
            "is_4k": (width, height) == (3840, 2160),
            "actual": {"width": width, "height": height, "backend": "MSMF"}
            if width > 0
            else None,
            "error": None if width > 0 else "open failed",
        }

    monkeypatch.setattr("app.camera_selection.importlib.import_module", lambda name: object())
    monkeypatch.setattr("app.camera_selection._probe_index_fast", fake_probe)
    settings = CameraSettings(device_index=0)

    result = select_startup_camera(settings)

    assert result["selected"] is True
    assert result["mode"] == "strict_first_4k"
    assert result["device_index"] == 2
    assert settings.device_index == 2
    assert attempts == [0, 1, 2]


def test_rejects_non_4k_cameras_and_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "2")

    def fake_probe(cv2, settings, device_index):
        return {
            "device_index": device_index,
            "opened": True,
            "frame_read": True,
            "is_4k": False,
            "actual": {"width": 1920, "height": 1080, "backend": "MSMF"},
            "error": None,
        }

    monkeypatch.setattr("app.camera_selection.importlib.import_module", lambda name: object())
    monkeypatch.setattr("app.camera_selection._probe_index_fast", fake_probe)
    settings = CameraSettings(device_index=1)

    with pytest.raises(RequiredCameraUnavailableError, match="3840x2160"):
        select_startup_camera(settings)

    assert settings.device_index == 1


def test_scan_limit_is_respected(monkeypatch) -> None:
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "1")
    attempts: list[int] = []

    def fake_probe(cv2, settings, device_index):
        attempts.append(device_index)
        return {
            "device_index": device_index,
            "opened": False,
            "frame_read": False,
            "is_4k": False,
            "error": "open failed",
        }

    monkeypatch.setattr("app.camera_selection.importlib.import_module", lambda name: object())
    monkeypatch.setattr("app.camera_selection._probe_index_fast", fake_probe)

    with pytest.raises(RequiredCameraUnavailableError):
        select_startup_camera(CameraSettings())

    assert attempts == [0, 1]
