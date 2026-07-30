from __future__ import annotations

import importlib
import statistics
import subprocess
import time
from typing import Any

from app.config import CameraProbeMode, CameraSettings, GpuSettings


def _run_nvidia_smi(timeout_seconds: float) -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total,memory.used,temperature.gpu,pstate",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return {"available": False, "error": "nvidia-smi was not found", "gpus": []}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "nvidia-smi timed out", "gpus": []}

    if completed.returncode != 0:
        return {
            "available": False,
            "error": completed.stderr.strip() or f"nvidia-smi exited {completed.returncode}",
            "gpus": [],
        }

    gpus: list[dict[str, Any]] = []
    for index, line in enumerate(completed.stdout.splitlines()):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 6:
            continue
        name, driver, total, used, temperature, pstate = parts
        gpus.append(
            {
                "index": index,
                "name": name,
                "driver_version": driver,
                "memory_total_mb": _number_or_text(total),
                "memory_used_mb": _number_or_text(used),
                "temperature_c": _number_or_text(temperature),
                "performance_state": pstate,
            }
        )
    return {"available": True, "error": None, "gpus": gpus}


def _number_or_text(value: str) -> int | float | str:
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def probe_gpu(settings: GpuSettings) -> dict[str, Any]:
    errors: list[str] = []
    ort_info: dict[str, Any]
    try:
        ort = importlib.import_module("onnxruntime")
        providers = list(ort.get_available_providers())
        ort_info = {
            "installed": True,
            "version": getattr(ort, "__version__", "unknown"),
            "available_providers": providers,
        }
    except Exception as exc:
        providers = []
        ort_info = {
            "installed": False,
            "version": None,
            "available_providers": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
        errors.append("onnxruntime is unavailable")

    cuda_available = "CUDAExecutionProvider" in providers
    if not cuda_available:
        errors.append("CUDAExecutionProvider is unavailable")

    smi_info = _run_nvidia_smi(settings.nvidia_smi_timeout_seconds)
    if not smi_info["available"]:
        errors.append(str(smi_info.get("error") or "nvidia-smi probe failed"))

    ok = cuda_available and bool(smi_info["available"])
    return {
        "ok": ok,
        "required": settings.require_cuda,
        "status": "ready" if ok else "degraded",
        "cuda_execution_provider": cuda_available,
        "onnxruntime": ort_info,
        "nvidia_smi": smi_info,
        "errors": errors,
        "probed_at_unix": time.time(),
    }


def _decode_fourcc(value: float) -> str:
    integer = int(value)
    chars = [chr((integer >> (8 * index)) & 0xFF) for index in range(4)]
    decoded = "".join(chars).strip("\x00 ")
    return decoded or "unknown"


def _backend_code(cv2: Any, name: str) -> int:
    if name == "DSHOW":
        return int(getattr(cv2, "CAP_DSHOW", getattr(cv2, "CAP_ANY", 0)))
    if name == "MSMF":
        return int(getattr(cv2, "CAP_MSMF", getattr(cv2, "CAP_ANY", 0)))
    return int(getattr(cv2, "CAP_ANY", 0))


def classify_camera_performance(
    measured_fps: float,
    *,
    ready_min_fps: float,
    degraded_min_fps: float,
) -> str:
    if measured_fps >= ready_min_fps:
        return "ready"
    if measured_fps >= degraded_min_fps:
        return "degraded"
    return "unusable_for_realtime"


def select_best_camera_attempt(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    successful = [attempt for attempt in attempts if attempt.get("successful")]
    if not successful:
        return None

    status_rank = {
        "ready": 3,
        "degraded": 2,
        "unusable_for_realtime": 1,
    }

    def score(attempt: dict[str, Any]) -> tuple[float, ...]:
        performance = attempt.get("performance") or {}
        sample = attempt.get("sample") or {}
        return (
            float(status_rank.get(str(attempt.get("status")), 0)),
            float(bool(performance.get("resolution_match"))),
            float(performance.get("measured_fps") or 0.0),
            float(sample.get("success_ratio") or 0.0),
            -float(sample.get("mean_read_ms") or float("inf")),
        )

    return max(successful, key=score)


def _requested_mode_payload(settings: CameraSettings, mode: CameraProbeMode) -> dict[str, Any]:
    return {
        "backend": mode.backend,
        "fourcc": mode.fourcc,
        "width": settings.requested_width,
        "height": settings.requested_height,
        "fps": mode.fps,
    }


def _probe_camera_mode(cv2: Any, settings: CameraSettings, mode: CameraProbeMode) -> dict[str, Any]:
    requested = _requested_mode_payload(settings, mode)
    backend = _backend_code(cv2, mode.backend)
    capture = None
    attempt: dict[str, Any] = {
        "requested": requested,
        "opened": False,
        "successful": False,
        "status": "unavailable",
        "error": None,
    }

    try:
        capture = cv2.VideoCapture(settings.device_index, backend)
        if not capture.isOpened():
            attempt["error"] = "open failed"
            return attempt

        attempt["opened"] = True
        set_results: dict[str, bool] = {}
        if mode.fourcc != "AUTO":
            fourcc_code = cv2.VideoWriter_fourcc(*mode.fourcc)
            set_results["fourcc"] = bool(capture.set(cv2.CAP_PROP_FOURCC, float(fourcc_code)))
        else:
            set_results["fourcc"] = True
        set_results["width"] = bool(
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(settings.requested_width))
        )
        set_results["height"] = bool(
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(settings.requested_height))
        )
        set_results["fps"] = bool(capture.set(cv2.CAP_PROP_FPS, float(mode.fps)))
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            set_results["buffer_size"] = bool(capture.set(cv2.CAP_PROP_BUFFERSIZE, 1))
        attempt["set_results"] = set_results

        for _ in range(settings.warmup_frames):
            capture.read()

        read_times_ms: list[float] = []
        latest_frame = None
        successful_reads = 0
        sample_started = time.perf_counter()
        for _ in range(settings.sample_frames):
            started = time.perf_counter()
            ok, frame = capture.read()
            read_times_ms.append((time.perf_counter() - started) * 1000.0)
            if ok and frame is not None:
                successful_reads += 1
                latest_frame = frame
        elapsed_seconds = max(time.perf_counter() - sample_started, 1e-9)

        if latest_frame is None:
            attempt["error"] = "camera opened but no frame was read"
            attempt["sample"] = {
                "requested_frames": settings.sample_frames,
                "successful_reads": successful_reads,
                "success_ratio": 0.0,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "mean_read_ms": round(statistics.fmean(read_times_ms), 3)
                if read_times_ms
                else None,
                "max_read_ms": round(max(read_times_ms), 3) if read_times_ms else None,
            }
            return attempt

        try:
            actual_backend = capture.getBackendName()
        except Exception:
            actual_backend = mode.backend

        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        reported_fps = float(capture.get(cv2.CAP_PROP_FPS))
        actual_fourcc = _decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC))
        measured_fps = successful_reads / elapsed_seconds
        resolution_match = (
            actual_width == settings.requested_width and actual_height == settings.requested_height
        )
        status = classify_camera_performance(
            measured_fps,
            ready_min_fps=settings.ready_min_measured_fps,
            degraded_min_fps=settings.degraded_min_measured_fps,
        )

        attempt.update(
            {
                "successful": True,
                "status": status,
                "actual": {
                    "width": actual_width,
                    "height": actual_height,
                    "reported_fps": reported_fps,
                    "fourcc": actual_fourcc,
                    "backend": actual_backend,
                    "frame_shape": list(latest_frame.shape),
                },
                "sample": {
                    "requested_frames": settings.sample_frames,
                    "successful_reads": successful_reads,
                    "success_ratio": round(successful_reads / settings.sample_frames, 4),
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "mean_read_ms": round(statistics.fmean(read_times_ms), 3),
                    "max_read_ms": round(max(read_times_ms), 3),
                },
                "performance": {
                    "measured_fps": round(measured_fps, 3),
                    "resolution_match": resolution_match,
                    "capture_realtime_capable": status == "ready",
                    "ready_min_measured_fps": settings.ready_min_measured_fps,
                    "degraded_min_measured_fps": settings.degraded_min_measured_fps,
                },
            }
        )
        return attempt
    except Exception as exc:
        attempt["error"] = f"{type(exc).__name__}: {exc}"
        return attempt
    finally:
        if capture is not None:
            capture.release()
        if settings.inter_attempt_delay_seconds > 0:
            time.sleep(settings.inter_attempt_delay_seconds)


def probe_camera(settings: CameraSettings) -> dict[str, Any]:
    if not settings.enabled:
        return {
            "ok": True,
            "status": "disabled",
            "enabled": False,
            "attempts": [],
            "probed_at_unix": time.time(),
        }

    try:
        cv2 = importlib.import_module("cv2")
    except Exception as exc:
        return {
            "ok": False,
            "status": "unavailable",
            "enabled": True,
            "error": f"OpenCV unavailable: {type(exc).__name__}: {exc}",
            "attempts": [],
            "probed_at_unix": time.time(),
        }

    attempts = [_probe_camera_mode(cv2, settings, mode) for mode in settings.probe_modes]
    selected = select_best_camera_attempt(attempts)
    requested = {
        "width": settings.requested_width,
        "height": settings.requested_height,
        "probe_modes": [mode.model_dump(mode="json") for mode in settings.probe_modes],
    }

    if selected is None:
        return {
            "ok": False,
            "status": "unavailable",
            "enabled": True,
            "device_index": settings.device_index,
            "requested": requested,
            "attempts": attempts,
            "error": "No configured camera mode produced a frame",
            "opencv_version": getattr(cv2, "__version__", "unknown"),
            "probed_at_unix": time.time(),
        }

    selected_status = str(selected["status"])
    result = {
        "ok": selected_status == "ready",
        "status": selected_status,
        "enabled": True,
        "device_index": settings.device_index,
        "requested": requested,
        "selected_mode": selected["requested"],
        "actual": selected["actual"],
        "sample": selected["sample"],
        "performance": selected["performance"],
        "attempts": attempts,
        "opencv_version": getattr(cv2, "__version__", "unknown"),
        "probed_at_unix": time.time(),
    }
    if selected_status != "ready":
        result["error"] = (
            "Camera produced frames, but no tested mode met the realtime threshold of "
            f"{settings.ready_min_measured_fps:g} measured FPS"
        )
    return result
