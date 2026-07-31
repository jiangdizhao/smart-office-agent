from __future__ import annotations

import pytest

import app.camera_selection as camera_selection
from app.config import CameraSettings


def _attempt(
    device_index: int,
    width: int = 0,
    height: int = 0,
    *,
    error: str | None = None,
) -> dict:
    available = width > 0 and height > 0
    return {
        "device_index": device_index,
        "opened": available,
        "frame_read": available,
        "is_4k": (width, height)
        == (camera_selection.REQUIRED_WIDTH, camera_selection.REQUIRED_HEIGHT),
        "actual": (
            {"width": width, "height": height, "backend": "MSMF"}
            if available
            else None
        ),
        "error": error if error is not None else (None if available else "open failed"),
    }


def test_selects_first_actual_4k_camera(monkeypatch) -> None:
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "4")
    attempts: list[int] = []
    resolutions = {
        0: (1280, 720),
        1: (1920, 1080),
        2: (3840, 2160),
        3: (3840, 2160),
    }

    def fake_probe(cv2, settings, device_index):
        attempts.append(device_index)
        width, height = resolutions.get(device_index, (0, 0))
        return _attempt(device_index, width, height)

    monkeypatch.setattr(camera_selection.importlib, "import_module", lambda name: object())
    monkeypatch.setattr(camera_selection, "_probe_index_fast", fake_probe)
    settings = CameraSettings(device_index=1)

    result = camera_selection.select_startup_camera(settings)

    assert result["mode"] == "first_verified_4k"
    assert result["selected"] is True
    assert result["device_index"] == 2
    assert settings.device_index == 2
    assert attempts == [0, 1, 2]
    assert len(result["attempts"]) == 3


def test_rejects_non_4k_cameras_and_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "2")

    def fake_probe(cv2, settings, device_index):
        return _attempt(device_index, 1920, 1080)

    monkeypatch.setattr(camera_selection.importlib, "import_module", lambda name: object())
    monkeypatch.setattr(camera_selection, "_probe_index_fast", fake_probe)
    settings = CameraSettings(device_index=1)

    with pytest.raises(RuntimeError, match="No camera produced the required 3840x2160 frame"):
        camera_selection.select_startup_camera(settings)

    assert settings.device_index == 1


def test_scan_limit_is_respected(monkeypatch) -> None:
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "1")
    attempts: list[int] = []

    def fake_probe(cv2, settings, device_index):
        attempts.append(device_index)
        return _attempt(device_index)

    monkeypatch.setattr(camera_selection.importlib, "import_module", lambda name: object())
    monkeypatch.setattr(camera_selection, "_probe_index_fast", fake_probe)
    settings = CameraSettings(device_index=0)

    with pytest.raises(RuntimeError):
        camera_selection.select_startup_camera(settings)

    assert attempts == [0, 1]


def test_invalid_scan_limit_uses_default(monkeypatch) -> None:
    monkeypatch.setenv("VISION_CAMERA_SCAN_MAX_INDEX", "invalid")
    assert camera_selection._scan_max_index() == camera_selection.DEFAULT_SCAN_MAX_INDEX


def test_probe_releases_capture_after_rejection() -> None:
    class Frame:
        shape = (1080, 1920, 3)

    class Capture:
        def __init__(self) -> None:
            self.released = False

        def isOpened(self) -> bool:
            return True

        def set(self, *args) -> bool:
            return True

        def read(self):
            return True, Frame()

        def getBackendName(self) -> str:
            return "MSMF"

        def release(self) -> None:
            self.released = True

    capture = Capture()

    class Cv2:
        CAP_ANY = 0
        CAP_MSMF = 1
        CAP_DSHOW = 2
        CAP_PROP_FOURCC = 6
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        CAP_PROP_BUFFERSIZE = 38

        @staticmethod
        def VideoCapture(device_index, backend):
            return capture

        @staticmethod
        def VideoWriter_fourcc(*characters):
            return 0

    result = camera_selection._probe_index_fast(Cv2(), CameraSettings(), 0)

    assert result["is_4k"] is False
    assert result["actual"] == {"width": 1920, "height": 1080, "backend": "MSMF"}
    assert capture.released is True
