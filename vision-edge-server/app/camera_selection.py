from __future__ import annotations

import importlib
import logging
import os
from typing import Any

from app.config import CameraSettings

logger = logging.getLogger(__name__)

REQUIRED_WIDTH = 3840
REQUIRED_HEIGHT = 2160
DEFAULT_SCAN_MAX_INDEX = 6
MAX_READ_ATTEMPTS = 3


def _scan_max_index() -> int:
    raw = os.getenv("VISION_CAMERA_SCAN_MAX_INDEX", str(DEFAULT_SCAN_MAX_INDEX)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SCAN_MAX_INDEX
    return max(0, min(value, 32))


def _backend_code(cv2: Any, backend: str) -> int:
    if backend == "DSHOW":
        return int(getattr(cv2, "CAP_DSHOW", getattr(cv2, "CAP_ANY", 0)))
    if backend == "MSMF":
        return int(getattr(cv2, "CAP_MSMF", getattr(cv2, "CAP_ANY", 0)))
    return int(getattr(cv2, "CAP_ANY", 0))


def _probe_index_fast(
    cv2: Any,
    settings: CameraSettings,
    device_index: int,
) -> dict[str, Any]:
    capture = None
    result: dict[str, Any] = {
        "device_index": device_index,
        "opened": False,
        "frame_read": False,
        "is_4k": False,
        "actual": None,
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
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(REQUIRED_WIDTH))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(REQUIRED_HEIGHT))
        capture.set(cv2.CAP_PROP_FPS, float(settings.runtime_fps))
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        frame = None
        for _ in range(MAX_READ_ATTEMPTS):
            ok, candidate = capture.read()
            if ok and candidate is not None:
                frame = candidate
                break

        if frame is None:
            result["error"] = "opened but no frame was read"
            return result

        height, width = frame.shape[:2]
        try:
            backend = capture.getBackendName()
        except Exception:
            backend = settings.runtime_backend
        result.update(
            {
                "frame_read": True,
                "is_4k": width == REQUIRED_WIDTH and height == REQUIRED_HEIGHT,
                "actual": {
                    "width": int(width),
                    "height": int(height),
                    "backend": backend,
                },
            }
        )
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass


def select_startup_camera(settings: CameraSettings) -> dict[str, Any]:
    """Select the first camera that actually returns a 3840x2160 frame.

    Each rejected capture is released before the next index is tried. The function
    raises RuntimeError when no scanned camera satisfies the strict 4K requirement,
    so the vision service cannot start on the wrong camera silently.
    """

    try:
        cv2 = importlib.import_module("cv2")
    except Exception as exc:
        raise RuntimeError(
            f"OpenCV is unavailable for 4K camera selection: {type(exc).__name__}: {exc}"
        ) from exc

    attempts: list[dict[str, Any]] = []
    for device_index in range(_scan_max_index() + 1):
        attempt = _probe_index_fast(cv2, settings, device_index)
        attempts.append(attempt)
        logger.info(
            "camera_4k_probe",
            extra={"event": "camera_4k_probe", **attempt},
        )
        if not attempt.get("is_4k"):
            continue

        settings.device_index = device_index
        result = {
            "mode": "first_verified_4k",
            "selected": True,
            "device_index": device_index,
            "required": {"width": REQUIRED_WIDTH, "height": REQUIRED_HEIGHT},
            "actual": attempt.get("actual"),
            "attempts": attempts,
        }
        logger.info(
            "camera_4k_selected",
            extra={
                "event": "camera_4k_selected",
                "device_index": device_index,
                "width": REQUIRED_WIDTH,
                "height": REQUIRED_HEIGHT,
            },
        )
        return result

    diagnostics = "; ".join(
        f"device {item['device_index']}: "
        + (
            f"{(item.get('actual') or {}).get('width')}x"
            f"{(item.get('actual') or {}).get('height')}"
            if item.get("actual")
            else str(item.get("error") or "unavailable")
        )
        for item in attempts
    )
    raise RuntimeError(
        f"No camera produced the required {REQUIRED_WIDTH}x{REQUIRED_HEIGHT} frame. "
        f"Scanned indices 0..{_scan_max_index()}. {diagnostics}"
    )
