from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from app.config import CameraSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FramePacket:
    frame_id: int
    captured_at_unix: float
    captured_at_monotonic: float
    frame: Any


class LatestFrameBuffer:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._packet: FramePacket | None = None

    def publish(self, packet: FramePacket) -> None:
        with self._condition:
            self._packet = packet
            self._condition.notify_all()

    def latest(self) -> FramePacket | None:
        with self._condition:
            return self._packet

    def wait_for_new(self, after_frame_id: int, timeout: float | None = None) -> FramePacket | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._packet is None or self._packet.frame_id <= after_frame_id:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._packet


class CameraManager:
    def __init__(self, settings: CameraSettings) -> None:
        self.settings = settings
        self.buffer = LatestFrameBuffer()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._capture: Any | None = None
        self._running = False
        self._open = False
        self._last_error: str | None = None
        self._frame_id = 0
        self._successful_reads = 0
        self._failed_reads = 0
        self._reconnect_count = 0
        self._consecutive_failures = 0
        self._max_consecutive_failures = 0
        self._started_monotonic: float | None = None
        self._last_frame_monotonic: float | None = None
        self._latencies_ms: deque[float] = deque(maxlen=settings.stats_window_frames)
        self._actual: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    def start(self) -> None:
        with self._state_lock:
            if self._running:
                return
            self._running = True
            self._last_error = None
            self._started_monotonic = time.monotonic()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, name="vision-camera", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._release_capture()
        with self._state_lock:
            self._running = False
            self._open = False
        self._thread = None

    def _backend_code(self, cv2: Any) -> int:
        if self.settings.runtime_backend == "DSHOW":
            return int(getattr(cv2, "CAP_DSHOW", getattr(cv2, "CAP_ANY", 0)))
        if self.settings.runtime_backend == "MSMF":
            return int(getattr(cv2, "CAP_MSMF", getattr(cv2, "CAP_ANY", 0)))
        return int(getattr(cv2, "CAP_ANY", 0))

    @staticmethod
    def _safe_fourcc(raw_value: float | int) -> tuple[int, str]:
        integer = int(raw_value)
        chars = [chr((integer >> (8 * index)) & 0xFF) for index in range(4)]
        decoded = "".join(chars).strip("\x00 ")
        if not decoded or any(ord(char) < 32 or ord(char) > 126 for char in decoded):
            return integer, "unknown"
        return integer, decoded

    def _open_capture(self, cv2: Any) -> bool:
        self._release_capture()
        capture = cv2.VideoCapture(self.settings.device_index, self._backend_code(cv2))
        if not capture.isOpened():
            capture.release()
            with self._state_lock:
                self._open = False
                self._last_error = "camera open failed"
            return False

        if self.settings.runtime_fourcc != "AUTO":
            capture.set(cv2.CAP_PROP_FOURCC, float(cv2.VideoWriter_fourcc(*self.settings.runtime_fourcc)))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.settings.requested_width))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.settings.requested_height))
        capture.set(cv2.CAP_PROP_FPS, float(self.settings.runtime_fps))
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        try:
            actual_backend = capture.getBackendName()
        except Exception:
            actual_backend = self.settings.runtime_backend
        fourcc_raw, fourcc = self._safe_fourcc(capture.get(cv2.CAP_PROP_FOURCC))
        actual = {
            "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "reported_fps": float(capture.get(cv2.CAP_PROP_FPS)),
            "backend": actual_backend,
            "fourcc_raw": fourcc_raw,
            "fourcc": fourcc,
        }
        self._capture = capture
        with self._state_lock:
            self._open = True
            self._last_error = None
            self._actual = actual
        logger.info("camera_runtime_opened", extra={"event": "camera_runtime_opened", "actual": actual})
        return True

    def _release_capture(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass

    def _capture_loop(self) -> None:
        try:
            import cv2
        except Exception as exc:
            with self._state_lock:
                self._last_error = f"OpenCV unavailable: {type(exc).__name__}: {exc}"
                self._running = False
            return

        while not self._stop_event.is_set():
            if self._capture is None and not self._open_capture(cv2):
                if self._stop_event.wait(self.settings.reconnect_delay_seconds):
                    break
                self._reconnect_count += 1
                continue

            capture = self._capture
            if capture is None:
                continue

            started = time.perf_counter()
            ok, frame = capture.read()
            latency_ms = (time.perf_counter() - started) * 1000.0
            now_monotonic = time.monotonic()
            now_unix = time.time()

            if ok and frame is not None:
                with self._state_lock:
                    self._frame_id += 1
                    frame_id = self._frame_id
                    self._successful_reads += 1
                    self._consecutive_failures = 0
                    self._last_frame_monotonic = now_monotonic
                    self._latencies_ms.append(latency_ms)
                    if self._actual is not None:
                        self._actual["frame_shape"] = list(frame.shape)
                self.buffer.publish(FramePacket(frame_id, now_unix, now_monotonic, frame))
                continue

            with self._state_lock:
                self._failed_reads += 1
                self._consecutive_failures += 1
                self._max_consecutive_failures = max(self._max_consecutive_failures, self._consecutive_failures)
                failures = self._consecutive_failures
                self._last_error = "camera read failed"
            if failures >= self.settings.reopen_after_consecutive_failures:
                logger.warning("camera_runtime_reopening", extra={"event": "camera_runtime_reopening", "consecutive_failures": failures})
                self._release_capture()
                with self._state_lock:
                    self._open = False
                    self._reconnect_count += 1
                if self._stop_event.wait(self.settings.reconnect_delay_seconds):
                    break

        self._release_capture()
        with self._state_lock:
            self._running = False
            self._open = False

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            now = time.monotonic()
            elapsed = max(now - self._started_monotonic, 1e-9) if self._started_monotonic is not None else 0.0
            mean_latency = statistics.fmean(self._latencies_ms) if self._latencies_ms else None
            measured_fps = self._successful_reads / elapsed if elapsed > 0 else 0.0
            frame_age_ms = (now - self._last_frame_monotonic) * 1000.0 if self._last_frame_monotonic is not None else None
            return {
                "running": self._running,
                "open": self._open,
                "device_index": self.settings.device_index,
                "requested": {
                    "backend": self.settings.runtime_backend,
                    "fourcc": self.settings.runtime_fourcc,
                    "width": self.settings.requested_width,
                    "height": self.settings.requested_height,
                    "fps": self.settings.runtime_fps,
                },
                "actual": dict(self._actual) if self._actual else None,
                "frame_id": self._frame_id,
                "frame_age_ms": round(frame_age_ms, 3) if frame_age_ms is not None else None,
                "successful_reads": self._successful_reads,
                "failed_reads": self._failed_reads,
                "measured_fps_since_start": round(measured_fps, 3),
                "rolling_mean_read_ms": round(mean_latency, 3) if mean_latency is not None else None,
                "reconnect_count": self._reconnect_count,
                "consecutive_failures": self._consecutive_failures,
                "max_consecutive_failures": self._max_consecutive_failures,
                "last_error": self._last_error,
            }
