from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.appearance import AppearanceExtractor
from app.camera_runtime import CameraManager
from app.config import AppConfig
from app.person_detector import DetectorUnavailableError, YoloXPersonDetector
from app.primary_lock import OcclusionAwarePrimarySelector
from app.tracking_runtime import MultiObjectTracker

logger = logging.getLogger(__name__)
EventCallback = Callable[[str, dict[str, Any]], None]


class VisionPipeline:
    def __init__(self, config: AppConfig, server_root: Path, emit_event: EventCallback) -> None:
        self.config = config
        self.camera = CameraManager(config.camera)
        self.detector = YoloXPersonDetector(config.detection, server_root)
        self.appearance = AppearanceExtractor(config.reid, server_root)
        self.tracker = MultiObjectTracker(
            config.tracking,
            config.presence,
            config.primary,
            self.appearance,
        )
        self.tracker.primary = OcclusionAwarePrimarySelector(config.primary, config.presence)
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
        self._last_tracks: list[dict[str, Any]] = []
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
            if not self.config.vision.enabled:
                self._status = "disabled"
                self._last_error = None
                return
            if not self.config.camera.enabled:
                self._status = "degraded"
                self._last_error = "camera is disabled"
                return
            self._running = True
            self._status = "starting"
            self._last_error = None
            self._last_frame_id = 0
            self._last_detections = []
            self._last_tracks = []
        self._stop_event.clear()
        self.tracker.reset()
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

        try:
            self.appearance.load()
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._status = "degraded"
                self._last_error = detail
            self.emit_event("server_degraded", {"component": "appearance", "detail": detail})

        self._thread = threading.Thread(target=self._run_loop, name="vision-pipeline", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self.camera.stop(timeout=timeout)
        self.detector.close()
        self.appearance.close()
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
            while next_run <= now:
                next_run += period

            resize_started = time.perf_counter()
            global_frame = cv2.resize(
                packet.frame,
                (self.config.vision.global_width, self.config.vision.global_height),
                interpolation=cv2.INTER_AREA,
            )
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

            tracking_started = time.perf_counter()
            tracks: list[dict[str, Any]] = []
            if self.config.tracking.enabled:
                try:
                    tracks, events = self.tracker.update(
                        detections,
                        packet.frame,
                        now=packet.captured_at_monotonic,
                    )
                    for event_type, payload in events:
                        self.emit_event(event_type, payload)
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                    with self._lock:
                        self._status = "degraded"
                        self._last_error = detail
                    logger.exception("multi_object_tracking_failed")
            tracking_ms = (time.perf_counter() - tracking_started) * 1000.0

            preview_ms = 0.0
            encoded_bytes: bytes | None = None
            if self.config.debug.enabled:
                preview_started = time.perf_counter()
                preview = self._annotate(global_frame, detections, tracks)
                if (
                    preview.shape[1] != self.config.debug.preview_width
                    or preview.shape[0] != self.config.debug.preview_height
                ):
                    preview = cv2.resize(
                        preview,
                        (self.config.debug.preview_width, self.config.debug.preview_height),
                        interpolation=cv2.INTER_AREA,
                    )
                encode_ok, encoded = cv2.imencode(
                    ".jpg",
                    preview,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self.config.debug.jpeg_quality],
                )
                preview_ms = (time.perf_counter() - preview_started) * 1000.0
                if encode_ok:
                    encoded_bytes = encoded.tobytes()

            with self._lock:
                self._processed_frames += 1
                self._last_detections = detections
                self._last_tracks = tracks
                self._last_timings = {
                    "resize_ms": round(resize_ms, 3),
                    **detector_timings,
                    "tracking_ms": round(tracking_ms, 3),
                    "preview_ms": round(preview_ms, 3),
                }
                self._last_processed_unix = time.time()
                if encoded_bytes is not None:
                    self._preview_jpeg = encoded_bytes
                detector_ok = not self.config.detection.enabled or self.detector.ready
                appearance_ok = not self.config.reid.enabled or self.appearance.ready
                if detector_ok and appearance_ok and self.camera.running:
                    self._status = "ready"
                    self._last_error = None
        with self._lock:
            self._running = False

    def _annotate(
        self,
        frame: Any,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
    ) -> Any:
        import cv2
        import numpy as np

        output = frame.copy()
        height, width = output.shape[:2]
        if self.config.debug.draw_zone:
            points = np.array(
                [[int(x * width), int(y * height)] for x, y in self.config.presence.engagement_zone],
                dtype=np.int32,
            )
            cv2.polylines(output, [points], isClosed=True, color=(0, 255, 255), thickness=2)

        if self.config.debug.draw_raw_detections:
            for detection in detections:
                x1, y1, x2, y2 = _bbox_pixels(detection["bbox"], width, height)
                cv2.rectangle(output, (x1, y1), (x2, y2), (160, 160, 160), 1)

        for track in tracks:
            state = track["state"]
            if state == "lost" and not self.config.debug.draw_lost_tracks:
                continue
            x1, y1, x2, y2 = _bbox_pixels(track["bbox"], width, height)
            if track["primary"]:
                color = (255, 0, 255)
                thickness = 4
            elif state == "lost":
                color = (0, 165, 255)
                thickness = 2
            elif state == "tentative":
                color = (255, 255, 0)
                thickness = 2
            else:
                color = (0, 255, 0)
                thickness = 2
            cv2.rectangle(output, (x1, y1), (x2, y2), color, thickness)
            label = (
                f"ID {track['track_id']} {state} "
                f"score={track['score']:.2f} area={track['area_ratio']:.3f}"
            )
            if track["primary"]:
                label += " PRIMARY"
            if track["engaged"]:
                label += " ENGAGED"
            cv2.putText(
                output,
                label,
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                color,
                2,
                cv2.LINE_AA,
            )
            if self.config.debug.draw_track_trails and len(track["trail"]) >= 2:
                trail = np.asarray(
                    [[int(x * width), int(y * height)] for x, y in track["trail"]],
                    dtype=np.int32,
                )
                cv2.polylines(output, [trail], isClosed=False, color=color, thickness=2)

        tracker_snapshot = self.tracker.snapshot()
        appearance_backend = tracker_snapshot["appearance"]["backend"]
        lines = [
            (
                f"phase2 state={tracker_snapshot['scene_state']} "
                f"people={tracker_snapshot['person_count']} "
                f"tracks={tracker_snapshot['track_count']} "
                f"primary={tracker_snapshot['primary_track_id']}"
            ),
            (
                f"frame={self._last_frame_id} detector={len(detections)} "
                f"appearance={appearance_backend} tracker={tracker_snapshot['last_update_ms']}ms"
            ),
        ]
        for index, text in enumerate(lines):
            y = 28 + index * 26
            cv2.putText(
                output,
                text,
                (12, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
        return output

    def latest_preview(self) -> bytes | None:
        with self._lock:
            return self._preview_jpeg

    def detections_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "frame_id": self._last_frame_id,
                "processed_at_unix": self._last_processed_unix,
                "raw_person_count": len(self._last_detections),
                "detections": list(self._last_detections),
                "tracking": self.tracker.snapshot(),
                "timings": dict(self._last_timings) if self._last_timings else None,
            }

    def tracks_snapshot(self) -> dict[str, Any]:
        return self.tracker.snapshot()

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
        status["tracking"] = self.tracker.snapshot()
        return status


def _bbox_pixels(box: dict[str, float], width: int, height: int) -> tuple[int, int, int, int]:
    x1 = int(_clip(box["x"]) * width)
    y1 = int(_clip(box["y"]) * height)
    x2 = int(_clip(box["x"] + box["width"]) * width)
    y2 = int(_clip(box["y"] + box["height"]) * height)
    return x1, y1, x2, y2


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
