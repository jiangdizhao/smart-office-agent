from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.appearance import AppearanceExtractor
from app.camera_runtime import CameraManager
from app.config import AppConfig
from app.face_runtime import FaceRuntime, FaceRuntimeError
from app.identity_runtime import IdentityRuntime, IdentityRuntimeError
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
        self.face = FaceRuntime(config.face, server_root)
        self.identity = IdentityRuntime(config.identity, server_root)
        self.emit_event = emit_event
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._running = False
        self._status = "stopped"
        self._last_error: str | None = None
        self._last_frame_id = 0
        self._processed_frames = 0
        self._skipped_frames = 0
        self._last_detections: list[dict[str, Any]] = []
        self._last_tracks: list[dict[str, Any]] = []
        self._last_faces: list[dict[str, Any]] = []
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
            self._last_faces = []
        self._stop_event.clear()
        self.tracker.reset()
        self.camera.start()

        self._load_component(
            "person_detector",
            self.config.detection.enabled,
            self.detector.load,
            DetectorUnavailableError,
        )
        self._load_component("appearance", self.config.reid.enabled, self.appearance.load, Exception)
        self._load_component("face_detector", self.config.face.enabled, self.face.load, FaceRuntimeError)
        self._load_component(
            "face_identity",
            self.config.identity.enabled,
            self.identity.load,
            IdentityRuntimeError,
        )

        self._thread = threading.Thread(target=self._run_loop, name="vision-pipeline", daemon=True)
        self._thread.start()

    def _load_component(
        self,
        component: str,
        enabled: bool,
        loader: Callable[[], None],
        expected_error: type[Exception],
    ) -> None:
        if not enabled:
            return
        try:
            loader()
        except expected_error as exc:
            detail = str(exc)
            with self._lock:
                self._status = "degraded"
                self._last_error = detail
            self.emit_event("server_degraded", {"component": component, "detail": detail})
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._status = "degraded"
                self._last_error = detail
            self.emit_event("server_degraded", {"component": component, "detail": detail})

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self.camera.stop(timeout=timeout)
        self.detector.close()
        self.appearance.close()
        self.face.close()
        self.identity.close()
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
                    self._degrade("person_detection_failed", exc)

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
                    self._degrade("multi_object_tracking_failed", exc)
            tracking_ms = (time.perf_counter() - tracking_started) * 1000.0

            face_started = time.perf_counter()
            faces: list[dict[str, Any]] = []
            if self.config.face.enabled and self.face.ready:
                try:
                    faces, face_events = self.face.process(
                        packet.frame,
                        tracks,
                        frame_id=packet.frame_id,
                        now_monotonic=packet.captured_at_monotonic,
                    )
                    for event_type, payload in face_events:
                        self.emit_event(event_type, payload)
                except Exception as exc:
                    self.face.last_error = f"{type(exc).__name__}: {exc}"
                    self._degrade("face_detection_failed", exc)
            face_ms = (time.perf_counter() - face_started) * 1000.0

            identity_started = time.perf_counter()
            if self.config.identity.enabled and self.identity.ready:
                try:
                    self._process_identities(packet.frame, tracks, faces, packet.captured_at_monotonic)
                except Exception as exc:
                    self.identity.last_error = f"{type(exc).__name__}: {exc}"
                    self._degrade("face_identity_failed", exc)
            active_track_ids = {int(track["track_id"]) for track in tracks}
            self.identity.cleanup_tracks(active_track_ids)
            tracks = self._enrich_tracks(tracks, faces)
            identity_ms = (time.perf_counter() - identity_started) * 1000.0

            preview_ms = 0.0
            encoded_bytes: bytes | None = None
            if self.config.debug.enabled:
                preview_started = time.perf_counter()
                preview = self._annotate(global_frame, detections, tracks, faces)
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
                self._last_faces = faces
                self._last_timings = {
                    "resize_ms": round(resize_ms, 3),
                    **detector_timings,
                    "tracking_ms": round(tracking_ms, 3),
                    "face_ms": round(face_ms, 3),
                    "identity_ms": round(identity_ms, 3),
                    "preview_ms": round(preview_ms, 3),
                }
                self._last_processed_unix = time.time()
                if encoded_bytes is not None:
                    self._preview_jpeg = encoded_bytes
                if self._components_ready() and self.camera.running:
                    self._status = "ready"
                    self._last_error = None
        with self._lock:
            self._running = False

    def _process_identities(
        self,
        source_frame: Any,
        tracks: list[dict[str, Any]],
        faces: list[dict[str, Any]],
        now_monotonic: float,
    ) -> None:
        track_state = {int(track["track_id"]): track for track in tracks}
        for face in faces:
            track_id = int(face["track_id"])
            track = track_state.get(track_id)
            if track is None or track.get("state") != "confirmed" or not face.get("ready"):
                continue
            face_row = self.face.face_row(track_id)
            if face_row is None:
                continue
            _, event = self.identity.process_track(
                track_id=track_id,
                source_frame=source_frame,
                face_row=face_row,
                quality_score=float(face["quality"]["score"]),
                now_monotonic=now_monotonic,
            )
            if event is not None:
                self.emit_event(event[0], event[1])

    def _enrich_tracks(
        self,
        tracks: list[dict[str, Any]],
        faces: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        faces_by_track = {int(face["track_id"]): face for face in faces}
        enriched = self.identity.enrich_tracks(tracks)
        for track in enriched:
            track["face"] = faces_by_track.get(int(track["track_id"]))
        return enriched

    def _degrade(self, message: str, exc: Exception) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        with self._lock:
            self._status = "degraded"
            self._last_error = detail
        logger.exception(message)

    def _components_ready(self) -> bool:
        return bool(
            (not self.config.detection.enabled or self.detector.ready)
            and (not self.config.reid.enabled or self.appearance.ready)
            and (not self.config.face.enabled or self.face.ready)
            and (not self.config.identity.enabled or self.identity.ready)
        )

    def _annotate(
        self,
        frame: Any,
        detections: list[dict[str, Any]],
        tracks: list[dict[str, Any]],
        faces: list[dict[str, Any]],
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
                color, thickness = (255, 0, 255), 4
            elif state == "lost":
                color, thickness = (0, 165, 255), 2
            elif state == "tentative":
                color, thickness = (255, 255, 0), 2
            else:
                color, thickness = (0, 255, 0), 2
            cv2.rectangle(output, (x1, y1), (x2, y2), color, thickness)
            label = (
                f"ID {track['track_id']} {state} "
                f"score={track['score']:.2f} area={track['area_ratio']:.3f}"
            )
            identity = track.get("identity")
            if identity and self.config.debug.draw_identity:
                label += f" NAME={identity['display_name']} sim={identity['similarity']:.2f}"
            if track["primary"]:
                label += " PRIMARY"
            if track["engaged"]:
                label += " ENGAGED"
            cv2.putText(
                output,
                label,
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.50,
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

        if self.config.debug.draw_faces:
            for face in faces:
                x1, y1, x2, y2 = _bbox_pixels(face["bbox"], width, height)
                face_color = (255, 180, 0) if face.get("ready") else (80, 140, 255)
                cv2.rectangle(output, (x1, y1), (x2, y2), face_color, 2)
                quality = face["quality"]
                identity = self.identity.result_for_track(int(face["track_id"]))
                face_label = (
                    f"face T{face['track_id']} q={quality['score']:.2f} "
                    f"sharp={quality['sharpness']:.0f} frontal={quality['frontal_score']:.2f}"
                )
                if identity and self.config.debug.draw_identity:
                    face_label += f" {identity['display_name']}"
                cv2.putText(
                    output,
                    face_label,
                    (x1, min(height - 8, y2 + 18)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    face_color,
                    1,
                    cv2.LINE_AA,
                )
                if self.config.debug.draw_face_landmarks:
                    for point in face["landmarks"]:
                        cv2.circle(
                            output,
                            (int(point["x"] * width), int(point["y"] * height)),
                            2,
                            face_color,
                            -1,
                        )

        tracker_snapshot = self.tracker.snapshot()
        identity_snapshot = self.identity.snapshot()
        lines = [
            (
                f"phase4 state={tracker_snapshot['scene_state']} "
                f"people={tracker_snapshot['person_count']} tracks={tracker_snapshot['track_count']} "
                f"primary={tracker_snapshot['primary_track_id']}"
            ),
            (
                f"frame={self._last_frame_id} detector={len(detections)} faces={len(faces)} "
                f"known={identity_snapshot['identity_count']} identified={len(identity_snapshot['recognized_tracks'])}"
            ),
        ]
        for index, text in enumerate(lines):
            cv2.putText(
                output,
                text,
                (12, 28 + index * 26),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.60,
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
                "tracking": self.tracks_snapshot(),
                "faces": self.face.snapshot(),
                "identity": self.identity.snapshot(),
                "timings": dict(self._last_timings) if self._last_timings else None,
            }

    def tracks_snapshot(self) -> dict[str, Any]:
        snapshot = self.tracker.snapshot()
        with self._lock:
            current_tracks = list(self._last_tracks)
        snapshot["tracks"] = current_tracks
        snapshot["face"] = self.face.snapshot()
        snapshot["identity"] = self.identity.snapshot()
        return snapshot

    def faces_snapshot(self) -> dict[str, Any]:
        snapshot = self.face.snapshot()
        snapshot["recognized_tracks"] = self.identity.snapshot()["recognized_tracks"]
        return snapshot

    def identities_snapshot(self) -> dict[str, Any]:
        return self.identity.snapshot()

    def enroll_identity(
        self,
        *,
        track_id: int,
        display_name: str,
        consent: bool,
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        identity_id: str | None = None,
    ) -> dict[str, Any]:
        face = self.face.face_observation(track_id)
        if face is None or not face.get("ready"):
            raise ValueError("the selected track does not have a stable high-quality face")
        identity = self.identity.enroll(
            track_id=track_id,
            display_name=display_name,
            consent=consent,
            external_id=external_id,
            metadata=metadata,
            identity_id=identity_id,
        )
        payload = {
            "track_id": track_id,
            "identity_id": identity["identity_id"],
            "display_name": identity["display_name"],
            "consent_at_unix": identity["consent_at_unix"],
        }
        self.emit_event("identity_enrolled", payload)
        return {"identity": identity, "track_id": track_id, "face": face}

    def delete_identity(self, identity_id: str) -> bool:
        deleted = self.identity.delete_identity(identity_id)
        if deleted:
            self.emit_event("identity_deleted", {"identity_id": identity_id})
        return deleted

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
        status["tracking"] = self.tracks_snapshot()
        status["face"] = self.face.snapshot()
        status["identity"] = self.identity.snapshot()
        return status


def _bbox_pixels(box: dict[str, float], width: int, height: int) -> tuple[int, int, int, int]:
    x1 = int(_clip(box["x"]) * width)
    y1 = int(_clip(box["y"]) * height)
    x2 = int(_clip(box["x"] + box["width"]) * width)
    y2 = int(_clip(box["y"] + box["height"]) * height)
    return x1, y1, x2, y2


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
