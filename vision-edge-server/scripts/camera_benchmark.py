from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.config import load_config


def _backend_code(cv2: Any, name: str) -> int:
    normalized = name.strip().upper()
    if normalized == "DSHOW":
        return int(getattr(cv2, "CAP_DSHOW", getattr(cv2, "CAP_ANY", 0)))
    if normalized == "MSMF":
        return int(getattr(cv2, "CAP_MSMF", getattr(cv2, "CAP_ANY", 0)))
    return int(getattr(cv2, "CAP_ANY", 0))


def _decode_fourcc(raw_value: float | int) -> tuple[int, str]:
    integer = int(raw_value)
    chars = [chr((integer >> (8 * index)) & 0xFF) for index in range(4)]
    decoded = "".join(chars).strip("\x00 ")
    if not decoded or any(ord(char) < 32 or ord(char) > 126 for char in decoded):
        return integer, "unknown"
    return integer, decoded


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _round_optional(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return SERVER_ROOT / "logs" / f"camera_benchmark_{stamp}.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a sustained camera capture benchmark and save a JSON report."
    )
    parser.add_argument("--config", default="", help="Path to vision.yaml")
    parser.add_argument("--duration-seconds", type=float, default=300.0)
    parser.add_argument("--warmup-seconds", type=float, default=3.0)
    parser.add_argument("--report-interval-seconds", type=float, default=10.0)
    parser.add_argument("--backend", choices=["MSMF", "DSHOW", "ANY"], default="MSMF")
    parser.add_argument("--fourcc", default="AUTO", help="AUTO or a four-character code")
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--device-index", type=int, default=-1)
    parser.add_argument("--output", default="", help="JSON report path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.duration_seconds <= 0:
        raise SystemExit("--duration-seconds must be greater than zero")
    if args.warmup_seconds < 0:
        raise SystemExit("--warmup-seconds cannot be negative")
    if args.report_interval_seconds <= 0:
        raise SystemExit("--report-interval-seconds must be greater than zero")
    fourcc = args.fourcc.strip().upper()
    if fourcc != "AUTO" and len(fourcc) != 4:
        raise SystemExit("--fourcc must be AUTO or exactly four characters")

    config, config_path = load_config(args.config or None)
    camera = config.camera
    device_index = camera.device_index if args.device_index < 0 else args.device_index
    width = camera.requested_width if args.width <= 0 else args.width
    height = camera.requested_height if args.height <= 0 else args.height
    output_path = Path(args.output).expanduser().resolve() if args.output else _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import cv2
    except Exception as exc:
        print(f"ERROR: OpenCV import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    requested = {
        "device_index": device_index,
        "backend": args.backend,
        "fourcc": fourcc,
        "width": width,
        "height": height,
        "fps": args.fps,
        "duration_seconds": args.duration_seconds,
        "warmup_seconds": args.warmup_seconds,
        "report_interval_seconds": args.report_interval_seconds,
    }

    print("Camera benchmark")
    print(f"Python: {sys.executable}")
    print(f"Config: {config_path}")
    print(
        "Requested: "
        f"device={device_index}, backend={args.backend}, fourcc={fourcc}, "
        f"resolution={width}x{height}, fps={args.fps:g}"
    )
    print(f"Duration: {args.duration_seconds:g} seconds")
    print(f"Report: {output_path}")

    capture = cv2.VideoCapture(device_index, _backend_code(cv2, args.backend))
    if not capture.isOpened():
        report = {
            "schema_version": "1.0",
            "started_at_utc": _utc_now(),
            "completed_at_utc": _utc_now(),
            "config_path": str(config_path),
            "requested": requested,
            "status": "unavailable",
            "error": "camera open failed",
        }
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("ERROR: Camera open failed", file=sys.stderr)
        return 1

    set_results: dict[str, bool] = {}
    try:
        if fourcc != "AUTO":
            set_results["fourcc"] = bool(
                capture.set(cv2.CAP_PROP_FOURCC, float(cv2.VideoWriter_fourcc(*fourcc)))
            )
        else:
            set_results["fourcc"] = True
        set_results["width"] = bool(capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(width)))
        set_results["height"] = bool(capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height)))
        set_results["fps"] = bool(capture.set(cv2.CAP_PROP_FPS, float(args.fps)))
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            set_results["buffer_size"] = bool(capture.set(cv2.CAP_PROP_BUFFERSIZE, 1))

        warmup_deadline = time.perf_counter() + args.warmup_seconds
        warmup_reads = 0
        while time.perf_counter() < warmup_deadline:
            capture.read()
            warmup_reads += 1

        try:
            actual_backend = capture.getBackendName()
        except Exception:
            actual_backend = args.backend
        fourcc_raw, actual_fourcc = _decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC))

        actual: dict[str, Any] = {
            "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "reported_fps": float(capture.get(cv2.CAP_PROP_FPS)),
            "fourcc_raw": fourcc_raw,
            "fourcc": actual_fourcc,
            "backend": actual_backend,
            "frame_shape": None,
        }

        started_at_utc = _utc_now()
        benchmark_started = time.perf_counter()
        deadline = benchmark_started + args.duration_seconds
        next_report = benchmark_started + args.report_interval_seconds

        read_latencies_ms: list[float] = []
        successful_reads = 0
        failed_reads = 0
        attempted_reads = 0
        consecutive_failures = 0
        max_consecutive_failures = 0
        reads_over_100_ms = 0
        reads_over_200_ms = 0
        resolution_changes = 0
        expected_shape: tuple[int, ...] | None = None
        interrupted = False

        try:
            while time.perf_counter() < deadline:
                read_started = time.perf_counter()
                ok, frame = capture.read()
                read_elapsed_ms = (time.perf_counter() - read_started) * 1000.0
                attempted_reads += 1
                read_latencies_ms.append(read_elapsed_ms)
                if read_elapsed_ms > 100.0:
                    reads_over_100_ms += 1
                if read_elapsed_ms > 200.0:
                    reads_over_200_ms += 1

                if ok and frame is not None:
                    successful_reads += 1
                    consecutive_failures = 0
                    current_shape = tuple(int(value) for value in frame.shape)
                    if expected_shape is None:
                        expected_shape = current_shape
                        actual["frame_shape"] = list(current_shape)
                    elif current_shape != expected_shape:
                        resolution_changes += 1
                        expected_shape = current_shape
                        actual["frame_shape"] = list(current_shape)
                else:
                    failed_reads += 1
                    consecutive_failures += 1
                    max_consecutive_failures = max(max_consecutive_failures, consecutive_failures)

                now = time.perf_counter()
                if now >= next_report:
                    elapsed = max(now - benchmark_started, 1e-9)
                    throughput = successful_reads / elapsed
                    print(
                        f"[{elapsed:7.1f}s] successful={successful_reads}, "
                        f"failed={failed_reads}, throughput={throughput:.2f} FPS, "
                        f"last_read={read_elapsed_ms:.1f} ms"
                    )
                    while next_report <= now:
                        next_report += args.report_interval_seconds
        except KeyboardInterrupt:
            interrupted = True
            print("Benchmark interrupted; saving partial report.")

        elapsed_seconds = max(time.perf_counter() - benchmark_started, 1e-9)
        success_ratio = successful_reads / attempted_reads if attempted_reads else 0.0
        measured_fps = successful_reads / elapsed_seconds
        mean_latency = statistics.fmean(read_latencies_ms) if read_latencies_ms else None
        p50_latency = _percentile(read_latencies_ms, 0.50)
        p95_latency = _percentile(read_latencies_ms, 0.95)
        p99_latency = _percentile(read_latencies_ms, 0.99)
        max_latency = max(read_latencies_ms) if read_latencies_ms else None

        if interrupted:
            status = "interrupted"
        elif (
            measured_fps >= camera.ready_min_measured_fps
            and success_ratio >= 0.99
            and max_consecutive_failures <= 3
        ):
            status = "ready"
        elif measured_fps >= camera.degraded_min_measured_fps and success_ratio >= 0.90:
            status = "degraded"
        else:
            status = "unusable_for_realtime"

        report = {
            "schema_version": "1.0",
            "started_at_utc": started_at_utc,
            "completed_at_utc": _utc_now(),
            "config_path": str(config_path),
            "opencv_version": getattr(cv2, "__version__", "unknown"),
            "requested": requested,
            "set_results": set_results,
            "actual": actual,
            "warmup_reads": warmup_reads,
            "status": status,
            "summary": {
                "elapsed_seconds": round(elapsed_seconds, 3),
                "attempted_reads": attempted_reads,
                "successful_reads": successful_reads,
                "failed_reads": failed_reads,
                "success_ratio": round(success_ratio, 6),
                "measured_fps": round(measured_fps, 3),
                "latency_ms": {
                    "mean": _round_optional(mean_latency),
                    "p50": _round_optional(p50_latency),
                    "p95": _round_optional(p95_latency),
                    "p99": _round_optional(p99_latency),
                    "max": _round_optional(max_latency),
                },
                "reads_over_100_ms": reads_over_100_ms,
                "reads_over_200_ms": reads_over_200_ms,
                "max_consecutive_failures": max_consecutive_failures,
                "resolution_changes": resolution_changes,
                "interrupted": interrupted,
            },
            "acceptance_thresholds": {
                "ready_min_measured_fps": camera.ready_min_measured_fps,
                "degraded_min_measured_fps": camera.degraded_min_measured_fps,
                "ready_min_success_ratio": 0.99,
                "degraded_min_success_ratio": 0.90,
                "ready_max_consecutive_failures": 3,
            },
        }
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        print("\nBenchmark complete")
        print(f"Status: {status}")
        print(f"Measured throughput: {measured_fps:.3f} FPS")
        print(f"Successful reads: {successful_reads}/{attempted_reads} ({success_ratio:.2%})")
        print(
            "Latency ms: "
            f"mean={_round_optional(mean_latency)}, p50={_round_optional(p50_latency)}, "
            f"p95={_round_optional(p95_latency)}, p99={_round_optional(p99_latency)}, "
            f"max={_round_optional(max_latency)}"
        )
        print(f"Reads >100 ms: {reads_over_100_ms}; >200 ms: {reads_over_200_ms}")
        print(f"Maximum consecutive failures: {max_consecutive_failures}")
        print(f"Resolution changes: {resolution_changes}")
        print(f"Report saved: {output_path}")
        return 0
    finally:
        capture.release()


if __name__ == "__main__":
    raise SystemExit(main())
