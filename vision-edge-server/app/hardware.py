from __future__ import annotations

import importlib
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.config import CameraSettings, GpuSettings


@dataclass
class ProbeAttempt:
    backend: str
    opened: bool
    error: str | None = None


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

    attempts: list[ProbeAttempt] = []
    for backend_name in settings.preferred_backends:
        backend = _backend_code(cv2, backend_name)
        capture = None
        try:
            capture = cv2.VideoCapture(settings.device_index, backend)
            if not capture.isOpened():
                attempts.append(ProbeAttempt(backend=backend_name, opened=False, error="open failed"))
                continue

            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(settings.requested_width))
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(settings.requested_height))
            capture.set(cv2.CAP_PROP_FPS, float(settings.requested_fps))
            if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            for _ in range(settings.warmup_frames):
                capture.read()

            read_times_ms: list[float] = []
            latest_frame = None
            successful_reads = 0
            for _ in range(settings.sample_frames):
                started = time.perf_counter()
                ok, frame = capture.read()
                read_times_ms.append((time.perf_counter() - started) * 1000.0)
                if ok and frame is not None:
                    successful_reads += 1
                    latest_frame = frame

            if latest_frame is None:
                attempts.append(
                    ProbeAttempt(backend=backend_name, opened=True, error="camera opened but no frame was read")
                )
                continue

            try:
                actual_backend = capture.getBackendName()
            except Exception:
                actual_backend = backend_name

            attempts.append(ProbeAttempt(backend=backend_name, opened=True, error=None))
            return {
                "ok": True,
                "status": "ready",
                "enabled": True,
                "device_index": settings.device_index,
                "requested": {
                    "width": settings.requested_width,
                    "height": settings.requested_height,
                    "fps": settings.requested_fps,
                },
                "actual": {
                    "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "fps": float(capture.get(cv2.CAP_PROP_FPS)),
                    "fourcc": _decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC)),
                    "backend": actual_backend,
                    "frame_shape": list(latest_frame.shape),
                },
                "sample": {
                    "requested_frames": settings.sample_frames,
                    "successful_reads": successful_reads,
                    "mean_read_ms": round(statistics.fmean(read_times_ms), 3),
                    "max_read_ms": round(max(read_times_ms), 3),
                },
                "attempts": [asdict(item) for item in attempts],
                "opencv_version": getattr(cv2, "__version__", "unknown"),
                "probed_at_unix": time.time(),
            }
        except Exception as exc:
            attempts.append(
                ProbeAttempt(backend=backend_name, opened=False, error=f"{type(exc).__name__}: {exc}")
            )
        finally:
            if capture is not None:
                capture.release()

    return {
        "ok": False,
        "status": "unavailable",
        "enabled": True,
        "device_index": settings.device_index,
        "requested": {
            "width": settings.requested_width,
            "height": settings.requested_height,
            "fps": settings.requested_fps,
        },
        "attempts": [asdict(item) for item in attempts],
        "error": "No configured camera backend produced a frame",
        "probed_at_unix": time.time(),
    }
