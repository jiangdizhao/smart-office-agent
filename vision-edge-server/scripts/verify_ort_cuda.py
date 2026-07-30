from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.ort_cuda import prepare_cuda_runtime


def _resolve_shape(shape: list[Any]) -> tuple[int, ...]:
    resolved: list[int] = []
    for index, value in enumerate(shape):
        if isinstance(value, int) and value > 0:
            resolved.append(value)
        elif index == 0:
            resolved.append(1)
        elif index == 1:
            resolved.append(3)
        else:
            resolved.append(416)
    return tuple(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an actual ONNX Runtime CUDA session and inference.")
    parser.add_argument("--model", default="models/yolox_nano.onnx")
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = (SERVER_ROOT / model_path).resolve()
    if not model_path.exists():
        print(f"FAIL: model not found: {model_path}", file=sys.stderr)
        return 1

    try:
        import onnxruntime as ort
    except Exception as exc:
        print(f"FAIL: ONNX Runtime import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    runtime = prepare_cuda_runtime(ort)
    report: dict[str, Any] = {
        "python": sys.executable,
        "onnxruntime_version": getattr(ort, "__version__", "unknown"),
        "available_providers": list(ort.get_available_providers()),
        "cuda_runtime": runtime,
        "model": str(model_path),
    }

    try:
        session = ort.InferenceSession(
            str(model_path),
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        report["session_providers"] = list(session.get_providers())
        if "CUDAExecutionProvider" not in report["session_providers"]:
            raise RuntimeError(
                f"CUDA provider did not activate; session providers={report['session_providers']}"
            )

        model_input = session.get_inputs()[0]
        shape = _resolve_shape(list(model_input.shape))
        sample = np.zeros(shape, dtype=np.float32)
        started = time.perf_counter()
        outputs = session.run(None, {model_input.name: sample})
        report["inference_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        report["output_shapes"] = [list(np.asarray(output).shape) for output in outputs]
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("FAIL: CUDA session or test inference did not succeed.", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS: ONNX Runtime created a CUDA session and completed a test inference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
