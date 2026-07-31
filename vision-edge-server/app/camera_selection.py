from __future__ import annotations

import importlib
import logging
import os
import time
from typing import Any

from app.config import CameraSettings

logger = logging.getLogger(__name__)

DEFAULT_SCAN_MAX_INDEX = 6
TARGET_WIDTH = 3840
TARGET_HEIGHT = 2160


class RequiredCameraUnavailableError(RuntimeError):
    pass


def _scan_max_index() -> int:
    raw = os.getenv("VISION_CAMERA_SCAN_MAX_INDEX", str(DEFAULT_SCAN_MAX_INDEX)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SCAN_MAX_INDEX
    return max(0, min(value, 32))


def _backend_code(cv2: Any, name: str) -> int:
    if name == "DSHOW":
        return int(getattr(cv2, "CAP_DSHOW", getattr(cv2, "CAP_ANY", 0)))
    if name == "MSMF":
        return int(getattr(cv2, "CAP_MSMF", getattr(cv2, "CAP_ANY", 0)))
    return int(getattr(cv2, "CAP_ANY", 0))


def _decode_fourcc(raw_value: float | int) -> str:
    integer = int(raw_value)
    chars = [chr((integer >> (8 * index)) & 0xFF) for index in range(4)]
    decoded = "".join(chars).strip("\x00 ")
    if not decoded or any(ord(char) < 32 or ord(char) > 126 for char in decoded):
        return "unknown"
    return decoded


def _probe_index_fast(cv2: Any, settings: CameraSettings, device_index: int) -> dict[str, Any]:
    capture = None
    started = time.perf_counter()
    result: dict[str, Any] = {
        "device_index": device_index,
        "opened": False,
        "frame_read": False,
        "is_4k": False,
        "error": None,
    }
    try:
        capture = cv2.VideoCapture(
            device_index,
            _backend_code(cv2, settings.runtime_backend),
        )
        if not capture.isOpened():
            result["error"] = "open failed"
            return result
        result["opened"] = True

        if settings.runtime_fourcc != "AUTO":
            capture.set(
                cv2.CAP_PROP_FOURCC,
                float(cv2.VideoWriter_fourcc(*settings.runtime_fourcc)),
            )
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(TARGET_WIDTH))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(TARGET_HEIGHT))
        capture.set(cv2.CAP_PROP_FPS, float(settings.runtime_fps))
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        frame = None
        for _ in range(max(1, min(settings.warmup_frames + 1, 4))):
            ok, candidate = capture.read()
            if ok and candidate is not None:
                frame = candidate
        if frame is None:
            result["error"] = "opened but no frame was read"
            return result

        height, width = frame.shape[:2]
        try:
            backend = capture.getBackendName()
        except Exception:
            backend = settings.runtime_backend
        actual = {
            "width": int(width),
            "height": int(height),
            "backend": backend,
            "reported_fps": float(capture.get(cv2.CAP_PROP_FPS)),
            "fourcc": _decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC)),
            "frame_shape": list(frame.shape),
        }
        result.update(
            {
                "frame_read": True,
                "actual": actual,
                "is_4k": int(width) == TARGET_WIDTH and int(height) == TARGET_HEIGHT,
            }
        )
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if capture is not None:
            capture.release()
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)


def select_startup_camera(settings: CameraSettings) -> dict[str, Any]:
    """Select the first camera that actually delivers a 3840x2160 frame."""

    try:
        cv2 = importlib.import_module("cv2")
    except Exception as exc:
        raise RequiredCameraUnavailableError(
            f"OpenCV is unavailable: {type(exc).__name__}: {exc}"
        ) from exc

    attempts: list[dict[str, Any]] = []
    max_index = _scan_max_index()
    for device_index in range(max_index + 1):
        attempt = _probe_index_fast(cv2, settings, device_index)
        attempts.append(attempt)
        logger.info("camera_4k_probe", extra={"event": "camera_4k_probe", **attempt})
        if not attempt.get("is_4k"):
            continue

        settings.device_index = device_index
        actual = attempt.get("actual") or {}
        result = {
            "mode": "strict_first_4k",
            "selected": True,
            "required_resolution": [TARGET_WIDTH, TARGET_HEIGHT],
            "device_index": device_index,
            "actual": actual,
            "runtime_backend": settings.runtime_backend,
            "runtime_fourcc": settings.runtime_fourcc,
            "runtime_fps": settings.runtime_fps,
            "attempts": attempts,
        }
        logger.info(
            "camera_4k_selected",
            extra={
                "event": "camera_4k_selected",
                "device_index": device_index,
                "width": actual.get("width"),
                "height": actual.get("height"),
                "backend": actual.get("backend"),
            },
        )
        return result

    observed = [
        {
            "device_index": item.get("device_index"),
            "actual": item.get("actual"),
            "error": item.get("error"),
        }
        for item in attempts
    ]
    message = (
        f"No camera produced the required {TARGET_WIDTH}x{TARGET_HEIGHT} frame. "
        f"Scanned indices 0..{max_index}. Observed: {observed}"
    )
    logger.error(
        "required_4k_camera_not_found",
        extra={
            "event": "required_4k_camera_not_found",
            "required_width": TARGET_WIDTH,
            "required_height": TARGET_HEIGHT,
            "attempts": attempts,
        },
    )
    raise RequiredCameraUnavailableError(message)
