from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.camera_runtime import CameraManager
from app.config import AppConfig
from app.person_detector import DetectorUnavailableError, YoloXPersonDetector
from app.presence import PresenceStateMachine

logger = logging.getLogger(__name__)
EventCallback = Callable[[str, dict[str, Any]], None]


class VisionPipeline:
    def __init__(self, config: AppConfig, server_root: Path, emit_event: EventCallback) -> None:
        self.config = config
        self.camera = CameraManager(config.camera)
        self.detector = YoloXPersonDetector(config.detection, server_root)
        self.presence = PresenceStateMachine(config.presence)
        self.emit_event = emit_event
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._status = "stopped"
        self._last_error: str | None = None
        self._last_frame_id = 0
        self._processed_frames = 0
        self._skipped_frames = 0
        self._last_detections: list[dict[str, Any]] = []
        self._last_timings: dict[str, Any] | None = None
        self._last_processed_unix: float | None = None
        self._preview_jpeg: bytes | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._status = "starting"
            self._last_error = None
        self._stop_event.clear()
        self.camera.start()
        if self.config.detection.enabled:
            try:
                self.detector.load()
            except DetectorUnavailableError as exc:
                self.detector.last_error = str(exc)
                with self._lock:
                    self._status = "degraded"
                    self._last_error = str(exc)
                self.emit_event("server_degraded", {"component": "person_detector", "detail": str(exc)})
        self._thread = threading.Thread(target=self._run_loop, name="vision-pipeline", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self.camera.stop(timeout=timeout)
        self.detector.close()
        with self._lock:
            self._running = False
            self._status = "stopped"
        self._thread = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def _run_loop(self) -> None:
        import cv2

        period = 1.0 / self.config.detection.inference_hz
        next_run = time.monotonic()
        while not self._stop_event.is_set():
            packet = self.camera.buffer.wait_for_new(self._last_frame_id, timeout=1.0)
            if packet is None:
                continue
            self._last_frame_id = packet.frame_id
            now = time.monotonic()
            if now < next_run:
                self._skipped_frames += 1
                continue
            next_run = now + period
            resize_started = time.perf_counter()
            global_frame = cv2.resize(packet.frame, (self.config.vision.global_width, self.config.vision.global_height), interpolation=cv2.INTER_AREA)
            resize_ms = (time.perf_counter() - resize_started) * 1000.0
            detections: list[dict[str, Any]] = []
            detector_timings: dict[str, Any] = {}
            if self.detector.ready:
                try:
                    detections, detector_timings = self.detector.detect(global_frame)
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                    self.detector.last_error = detail
                    with self._lock:
                        self._status = "degraded"
                        self._last_error = detail
                    logger.exception("person_detection_failed")
            for event_type, payload in self.presence.update(detections, now_monotonic=packet.captured_at_monotonic):
                self.emit_event(event_type, payload)
            preview_started = time.perf_counter()
            preview = self._annotate(global_frame, detections)
            encode_ok, encoded = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), self.config.debug.jpeg_quality])
            preview_ms = (time.perf_counter() - preview_started) * 1000.0
            with self._lock:
                self._processed_frames += 1
                self._last_detections = detections
                self._last_timings = {"resize_ms": round(resize_ms, 3), **detector_timings, "preview_ms": round(preview_ms, 3)}
                self._last_processed_unix = time.time()
                if encode_ok:
                    self._preview_jpeg = encoded.tobytes()
                if self.detector.ready and self.camera.running:
                    self._status = "ready"
                    self._last_error = None
        with self._lock:
            self._running = False

    def _annotate(self, frame: Any, detections: list[dict[str, Any]]) -> Any:
        import cv2
        import numpy as np

        output = frame.copy()
        height, width = output.shape[:2]
        if self.config.debug.draw_zone:
            points = np.array([[int(x * width), int(y * height)] for x, y in self.config.presence.engagement_zone], dtype=np.int32)
            cv2.polylines(output, [points], isClosed=True, color=(0, 255, 255), thickness=2)
        for index, detection in enumerate(detections):
            box = detection["bbox"]
            x1, y1 = int(box["x"] * width), int(box["y"] * height)
            x2 = int((box["x"] + box["width"]) * width)
            y2 = int((box["y"] + box["height"]) * height)
            cv2.rectangle(output, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"person {detection['score']:.2f} area={detection['area_ratio']:.3f}"
            cv2.putText(output, label, (x1, max(18, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA)
            cv2.putText(output, f"#{index + 1}", (x1 + 4, y1 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        cv2.putText(output, f"state={self.presence.state} people={len(detections)} frame={self._last_frame_id}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        return output

    def latest_preview(self) -> bytes | None:
        with self._lock:
            return self._preview_jpeg

    def detections_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "frame_id": self._last_frame_id,
                "processed_at_unix": self._last_processed_unix,
                "person_count": len(self._last_detections),
                "detections": list(self._last_detections),
                "presence": self.presence.snapshot(),
                "timings": dict(self._last_timings) if self._last_timings else None,
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            status = {
                "running": self._running,
                "status": self._status,
                "last_error": self._last_error,
                "processed_frames": self._processed_frames,
                "skipped_frames": self._skipped_frames,
                "last_frame_id": self._last_frame_id,
                "last_processed_unix": self._last_processed_unix,
                "last_timings": dict(self._last_timings) if self._last_timings else None,
            }
        status["camera"] = self.camera.status()
        status["detector"] = self.detector.status()
        status["presence"] = self.presence.snapshot()
        return status
