from __future__ import annotations

import logging
import os
from typing import Any

from app.config import CameraSettings
from app.hardware import probe_camera

logger = logging.getLogger(__name__)

DEFAULT_SCAN_MAX_INDEX = 6


def _manual_camera_index() -> int | None:
    raw = os.getenv("VISION_CAMERA_DEVICE_INDEX", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("VISION_CAMERA_DEVICE_INDEX must be a non-negative integer") from exc
    if value < 0:
        raise ValueError("VISION_CAMERA_DEVICE_INDEX must be a non-negative integer")
    return value


def _scan_max_index() -> int:
    raw = os.getenv("VISION_CAMERA_SCAN_MAX_INDEX", str(DEFAULT_SCAN_MAX_INDEX)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SCAN_MAX_INDEX
    return max(0, min(value, 32))


def _selection_score(result: dict[str, Any]) -> tuple[float, ...]:
    actual = result.get("actual") or {}
    performance = result.get("performance") or {}
    sample = result.get("sample") or {}
    width = float(actual.get("width") or 0)
    height = float(actual.get("height") or 0)
    status_rank = {"ready": 3.0, "degraded": 2.0, "unusable_for_realtime": 1.0}
    return (
        width * height,
        status_rank.get(str(result.get("status")), 0.0),
        float(performance.get("measured_fps") or 0.0),
        float(sample.get("success_ratio") or 0.0),
        -float(result.get("device_index") or 0),
    )


def _apply_selection(settings: CameraSettings, result: dict[str, Any]) -> None:
    settings.device_index = int(result["device_index"])
    selected_mode = result.get("selected_mode") or {}
    backend = str(selected_mode.get("backend") or settings.runtime_backend).upper()
    fourcc = str(selected_mode.get("fourcc") or settings.runtime_fourcc).upper()
    fps = float(selected_mode.get("fps") or settings.runtime_fps)
    if backend in {"DSHOW", "MSMF", "ANY"}:
        settings.runtime_backend = backend  # type: ignore[assignment]
    settings.runtime_fourcc = fourcc
    settings.runtime_fps = fps


def select_startup_camera(settings: CameraSettings) -> dict[str, Any]:
    """Select the camera with the highest actual output resolution.

    A manual VISION_CAMERA_DEVICE_INDEX override bypasses enumeration. Otherwise,
    indices 0..VISION_CAMERA_SCAN_MAX_INDEX are probed with the configured camera
    modes. Resolution is the primary ranking key; realtime status and measured FPS
    break ties. If no candidate produces frames, the configured device index is
    retained and the caller can attempt its normal runtime fallback.
    """

    configured_index = int(settings.device_index)
    manual_index = _manual_camera_index()
    if manual_index is not None:
        settings.device_index = manual_index
        result = {
            "mode": "manual_override",
            "selected": True,
            "device_index": manual_index,
            "configured_fallback_index": configured_index,
            "candidates": [],
        }
        logger.info("camera_startup_selection_manual", extra={"event": "camera_startup_selection_manual", **result})
        return result

    candidates: list[dict[str, Any]] = []
    for device_index in range(_scan_max_index() + 1):
        probe_settings = settings.model_copy(update={"device_index": device_index})
        probe = probe_camera(probe_settings)
        candidate = {
            "device_index": device_index,
            "status": probe.get("status"),
            "ok": bool(probe.get("ok")),
            "actual": probe.get("actual"),
            "selected_mode": probe.get("selected_mode"),
            "sample": probe.get("sample"),
            "performance": probe.get("performance"),
            "error": probe.get("error"),
        }
        candidates.append(candidate)
        logger.info(
            "camera_startup_candidate",
            extra={"event": "camera_startup_candidate", **candidate},
        )

    usable = [candidate for candidate in candidates if candidate.get("actual")]
    if not usable:
        result = {
            "mode": "automatic_highest_resolution",
            "selected": False,
            "device_index": configured_index,
            "configured_fallback_index": configured_index,
            "error": "No enumerated camera produced a frame",
            "candidates": candidates,
        }
        logger.warning("camera_startup_selection_fallback", extra={"event": "camera_startup_selection_fallback", **result})
        return result

    selected = max(usable, key=_selection_score)
    _apply_selection(settings, selected)
    actual = selected.get("actual") or {}
    result = {
        "mode": "automatic_highest_resolution",
        "selected": True,
        "device_index": settings.device_index,
        "configured_fallback_index": configured_index,
        "actual": actual,
        "selected_mode": selected.get("selected_mode"),
        "status": selected.get("status"),
        "performance": selected.get("performance"),
        "candidates": candidates,
    }
    logger.info(
        "camera_startup_selected",
        extra={
            "event": "camera_startup_selected",
            "device_index": settings.device_index,
            "width": actual.get("width"),
            "height": actual.get("height"),
            "backend": (selected.get("selected_mode") or {}).get("backend"),
            "fourcc": (selected.get("selected_mode") or {}).get("fourcc"),
            "fps": (selected.get("selected_mode") or {}).get("fps"),
            "status": selected.get("status"),
        },
    )
    return result
