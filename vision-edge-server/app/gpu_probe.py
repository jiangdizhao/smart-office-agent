from __future__ import annotations

import importlib
import subprocess
import time
from typing import Any

from app.config import GpuSettings
from app.ort_cuda import prepare_cuda_runtime


def _number_or_text(value: str) -> int | float | str:
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


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


def probe_gpu(settings: GpuSettings) -> dict[str, Any]:
    errors: list[str] = []
    providers: list[str] = []
    cuda_runtime: dict[str, Any] | None = None

    try:
        ort = importlib.import_module("onnxruntime")
        providers = list(ort.get_available_providers())
        cuda_advertised = "CUDAExecutionProvider" in providers
        cuda_runtime = prepare_cuda_runtime(ort) if cuda_advertised else None
        ort_info: dict[str, Any] = {
            "installed": True,
            "version": getattr(ort, "__version__", "unknown"),
            "available_providers": providers,
        }
    except Exception as exc:
        cuda_advertised = False
        ort_info = {
            "installed": False,
            "version": None,
            "available_providers": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
        errors.append("onnxruntime is unavailable")

    cuda_loadable = bool((cuda_runtime or {}).get("ready"))
    if not cuda_advertised:
        errors.append("CUDAExecutionProvider is not advertised by ONNX Runtime")
    elif not cuda_loadable:
        detail = (cuda_runtime or {}).get("provider_library", {}).get("error")
        errors.append(f"CUDAExecutionProvider runtime DLLs are not loadable: {detail}")

    smi_info = _run_nvidia_smi(settings.nvidia_smi_timeout_seconds)
    if not smi_info["available"]:
        errors.append(str(smi_info.get("error") or "nvidia-smi probe failed"))

    ok = cuda_advertised and cuda_loadable and bool(smi_info["available"])
    return {
        "ok": ok,
        "required": settings.require_cuda,
        "status": "ready" if ok else "degraded",
        "cuda_execution_provider": cuda_loadable,
        "cuda_execution_provider_advertised": cuda_advertised,
        "cuda_runtime": cuda_runtime,
        "onnxruntime": ort_info,
        "nvidia_smi": smi_info,
        "errors": errors,
        "probed_at_unix": time.time(),
    }
