from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Any


def preload_onnxruntime_dlls(ort: Any) -> dict[str, Any]:
    """Preload CUDA, cuDNN, and MSVC DLLs from Python site-packages when supported."""

    result: dict[str, Any] = {
        "supported": hasattr(ort, "preload_dlls"),
        "attempted": False,
        "ok": None,
        "directory": "",
        "error": None,
    }
    if not result["supported"]:
        return result

    result["attempted"] = True
    try:
        ort.preload_dlls(directory="")
        result["ok"] = True
    except Exception as exc:
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def probe_cuda_provider_library(ort: Any) -> dict[str, Any]:
    """Load the CUDA provider library itself so missing transitive DLLs are detected.

    ``get_available_providers()`` only reports providers compiled into the wheel. It does not
    prove that CUDA/cuDNN runtime DLL dependencies can be loaded on the current machine.
    """

    capi_dir = Path(ort.__file__).resolve().parent / "capi"
    if sys.platform == "win32":
        candidates = [capi_dir / "onnxruntime_providers_cuda.dll"]
    elif sys.platform.startswith("linux"):
        candidates = sorted(capi_dir.glob("libonnxruntime_providers_cuda.so*"))
    else:
        candidates = []

    library = next((path for path in candidates if path.exists()), None)
    result: dict[str, Any] = {
        "attempted": library is not None,
        "loadable": None,
        "library": str(library) if library else None,
        "error": None,
    }
    if library is None:
        result["loadable"] = False
        result["error"] = f"CUDA provider library was not found under {capi_dir}"
        return result

    directory_handle = None
    try:
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            directory_handle = os.add_dll_directory(str(capi_dir))
            ctypes.WinDLL(str(library))
        else:
            ctypes.CDLL(str(library))
        result["loadable"] = True
    except Exception as exc:
        result["loadable"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if directory_handle is not None:
            directory_handle.close()
    return result


def prepare_cuda_runtime(ort: Any) -> dict[str, Any]:
    preload = preload_onnxruntime_dlls(ort)
    provider_library = probe_cuda_provider_library(ort)
    return {
        "preload": preload,
        "provider_library": provider_library,
        "ready": bool(provider_library.get("loadable")),
    }
