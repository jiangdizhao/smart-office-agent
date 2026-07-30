from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from app.config import FaceSettings

MAX_DETECTOR_SIDE = 640


class FaceRuntimeError(RuntimeError):
    pass


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _bbox_pixels(box: dict[str, float], width: int, height: int) -> tuple[int, int, int, int]:
    x1 = int(_clip(box["x"]) * width)
    y1 = int(_clip(box["y"]) * height)
    x2 = int(_clip(box["x"] + box["width"]) * width)
    y2 = int(_clip(box["y"] + box["height"]) * height)
    return x1, y1, x2, y2


def analyse_face_quality(
    frame: np.ndarray,
    face_row: np.ndarray,
    settings: FaceSettings,
) -> dict[str, Any]:
    import cv2

    x, y, width, height = [float(value) for value in face_row[:4]]
    confidence = float(face_row[14])
    frame_height, frame_width = frame.shape[:2]
    x1 = max(0, min(frame_width - 1, int(x)))
    y1 = max(0, min(frame_height - 1, int(y)))
    x2 = max(x1 + 1, min(frame_width, int(x + width)))
    y2 = max(y1 + 1, min(frame_height, int(y + height)))
    crop = frame[y1:y2, x1:x2]
    if crop.size:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        brightness = float(np.mean(gray))
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    else:
        brightness = 0.0
        sharpness = 0.0

    landmarks = np.asarray(face_row[4:14], dtype=np.float64).reshape(5, 2)
    right_eye, left_eye, nose, right_mouth, left_mouth = landmarks
    eye_distance = float(np.linalg.norm(left_eye - right_eye))
    eye_midpoint = (left_eye + right_eye) / 2.0
    mouth_midpoint = (left_mouth + right_mouth) / 2.0
    roll_degrees = math.degrees(
        math.atan2(left_eye[1] - right_eye[1], left_eye[0] - right_eye[0])
    )
    nose_offset = abs(float(nose[0] - eye_midpoint[0])) / max(eye_distance, 1e-6)
    vertical_ratio = float(
        (nose[1] - eye_midpoint[1])
        / max(mouth_midpoint[1] - eye_midpoint[1], 1e-6)
    )
    roll_score = _clip(
        1.0 - abs(roll_degrees) / max(settings.max_abs_roll_degrees, 1e-6)
    )
    yaw_score = _clip(
        1.0 - nose_offset / max(settings.max_nose_offset_ratio, 1e-6)
    )
    vertical_score = _clip(1.0 - abs(vertical_ratio - 0.48) / 0.35)
    frontal_score = 0.45 * yaw_score + 0.35 * roll_score + 0.20 * vertical_score
    size_score = min(
        width / settings.min_face_width_pixels,
        height / settings.min_face_height_pixels,
        1.0,
    )
    sharpness_score = _clip(sharpness / max(settings.sharpness_full_scale, 1e-6))
    if brightness < settings.min_brightness:
        brightness_score = _clip(brightness / max(settings.min_brightness, 1e-6))
    elif brightness > settings.max_brightness:
        brightness_score = _clip(
            (255.0 - brightness) / max(255.0 - settings.max_brightness, 1e-6)
        )
    else:
        brightness_score = 1.0
    quality_score = (
        0.25 * confidence
        + 0.25 * size_score
        + 0.20 * sharpness_score
        + 0.20 * frontal_score
        + 0.10 * brightness_score
    )
    ready = bool(
        confidence >= settings.score_threshold
        and width >= settings.min_face_width_pixels
        and height >= settings.min_face_height_pixels
        and sharpness >= settings.min_sharpness
        and settings.min_brightness <= brightness <= settings.max_brightness
        and frontal_score >= settings.min_frontal_score
        and quality_score >= settings.min_quality_score
    )
    return {
        "score": round(quality_score, 6),
        "ready": ready,
        "face_confidence": round(confidence, 6),
        "width_pixels": round(width, 2),
        "height_pixels": round(height, 2),
        "sharpness": round(sharpness, 3),
        "brightness": round(brightness, 3),
        "frontal_score": round(frontal_score, 6),
        "roll_degrees": round(roll_degrees, 3),
        "nose_offset_ratio": round(nose_offset, 6),
    }


class FaceRuntime:
    def __init__(self, settings: FaceSettings, server_root: Path) -> None:
        self.settings = settings
        configured = Path(settings.model_path)
        self.model_path = configured if configured.is_absolute() else server_root / configured
        self.detector: Any | None = None
        self.last_error: str | None = None
        self.detect_count = 0
        self.last_detection_ms: float | None = None
        self._last_run_monotonic = 0.0
        self._faces: dict[int, dict[str, Any]] = {}
        self._face_rows: dict[int, np.ndarray] = {}
        self._ready_state: dict[int, bool] = {}
        self._lock = threading.RLock()

    @property
    def ready(self) -> bool:
        return not self.settings.enabled or self.detector is not None

    def load(self) -> None:
        if not self.settings.enabled:
            return
        if not self.model_path.exists():
            raise FaceRuntimeError(
                f"YuNet face model is missing: {self.model_path}. "
                "Run scripts/download_face_models.ps1."
            )
        try:
            import cv2

            if not hasattr(cv2, "FaceDetectorYN"):
                raise RuntimeError(f"OpenCV {cv2.__version__} does not provide FaceDetectorYN")
            self.detector = cv2.FaceDetectorYN.create(
                str(self.model_path),
                "",
                (320, 320),
                self.settings.score_threshold,
                self.settings.nms_threshold,
                self.settings.top_k,
            )
            self.last_error = None
        except Exception as exc:
            self.detector = None
            raise FaceRuntimeError(
                f"YuNet initialization failed: {type(exc).__name__}: {exc}"
            ) from exc

    def close(self) -> None:
        with self._lock:
            self.detector = None
            self._faces.clear()
            self._face_rows.clear()
            self._ready_state.clear()

    def should_run(self, now_monotonic: float) -> bool:
        if not self.settings.enabled or self.detector is None:
            return False
        return now_monotonic - self._last_run_monotonic >= 1.0 / self.settings.inference_hz

    def process(
        self,
        source_frame: np.ndarray,
        tracks: list[dict[str, Any]],
        *,
        frame_id: int,
        now_monotonic: float,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
        if not self.should_run(now_monotonic):
            self.expire(tracks, now_monotonic)
            return self.snapshot()["faces"], []
        self._last_run_monotonic = now_monotonic
        started = time.perf_counter()
        events: list[tuple[str, dict[str, Any]]] = []
        active_ids = {
            int(track["track_id"])
            for track in tracks
            if track.get("state") in {"confirmed", "tentative", "lost"}
        }
        candidates = [
            track
            for track in tracks
            if track.get("visible") and track.get("state") in {"confirmed", "tentative"}
        ]
        candidates.sort(
            key=lambda item: (
                not bool(item.get("primary")),
                -float(item.get("area_ratio", 0.0)),
            )
        )
        candidates = candidates[: self.settings.max_tracks_per_cycle]

        observed_ids: set[int] = set()
        with self._lock:
            for track in candidates:
                track_id = int(track["track_id"])
                observation, face_row = self._detect_track_face(
                    source_frame, track, frame_id, now_monotonic
                )
                if observation is None or face_row is None:
                    continue
                observed_ids.add(track_id)
                previous = self._faces.get(track_id)
                stable_frames = 1
                if previous is not None and previous.get("quality", {}).get("ready"):
                    stable_frames = int(previous.get("stable_frames", 0)) + 1
                observation["stable_frames"] = stable_frames
                observation["ready"] = bool(
                    observation["quality"]["ready"]
                    and stable_frames >= self.settings.ready_stable_frames
                )
                self._faces[track_id] = observation
                self._face_rows[track_id] = face_row
                was_ready = self._ready_state.get(track_id, False)
                is_ready = bool(observation["ready"])
                self._ready_state[track_id] = is_ready
                if is_ready and not was_ready:
                    events.append(("visitor_face_ready", self._public_face(observation)))

            for track_id in list(self._faces):
                if track_id not in active_ids:
                    self._remove_track(track_id)
                    continue
                if track_id not in observed_ids:
                    observation = self._faces[track_id]
                    age = now_monotonic - float(observation["last_seen_monotonic"])
                    if age > self.settings.stale_seconds:
                        if self._ready_state.get(track_id, False):
                            events.append(
                                (
                                    "visitor_face_lost",
                                    {"track_id": track_id, "age_seconds": round(age, 3)},
                                )
                            )
                        self._remove_track(track_id)

        self.detect_count += 1
        self.last_detection_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return self.snapshot()["faces"], events

    def _detect_track_face(
        self,
        source_frame: np.ndarray,
        track: dict[str, Any],
        frame_id: int,
        now_monotonic: float,
    ) -> tuple[dict[str, Any] | None, np.ndarray | None]:
        if self.detector is None:
            return None, None
        import cv2

        frame_height, frame_width = source_frame.shape[:2]
        box = dict(track["bbox"])
        margin_x = box["width"] * self.settings.person_crop_margin
        margin_y = box["height"] * self.settings.person_crop_margin
        crop_box = {
            "x": box["x"] - margin_x,
            "y": box["y"] - margin_y,
            "width": box["width"] + 2 * margin_x,
            "height": box["height"] + 2 * margin_y,
        }
        x1, y1, x2, y2 = _bbox_pixels(crop_box, frame_width, frame_height)
        if x2 - x1 < 32 or y2 - y1 < 32:
            return None, None
        crop = source_frame[y1:y2, x1:x2]

        # Bound YuNet cost for close visitors while retaining the 4K source coordinates.
        scale = min(1.0, MAX_DETECTOR_SIDE / max(crop.shape[0], crop.shape[1]))
        if scale < 1.0:
            detector_input = cv2.resize(
                crop,
                (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            detector_input = crop
        self.detector.setInputSize((detector_input.shape[1], detector_input.shape[0]))
        _, faces = self.detector.detect(detector_input)
        if faces is None or len(faces) == 0:
            return None, None

        selected: np.ndarray | None = None
        selected_rank: tuple[float, float] | None = None
        for raw_row in np.asarray(faces, dtype=np.float32):
            row = raw_row.copy()
            row[:14] /= max(scale, 1e-9)
            _, fy, fw, fh = [float(value) for value in row[:4]]
            center_y_ratio = (fy + fh / 2.0) / max(crop.shape[0], 1)
            if center_y_ratio > self.settings.max_face_center_y_ratio:
                continue
            rank = (float(row[14]), fw * fh)
            if selected_rank is None or rank > selected_rank:
                selected = row
                selected_rank = rank
        if selected is None:
            return None, None

        # YuNet row layout is [x, y, w, h, five landmark pairs, score].
        selected[[0, 4, 6, 8, 10, 12]] += x1
        selected[[1, 5, 7, 9, 11, 13]] += y1
        quality = analyse_face_quality(source_frame, selected, self.settings)
        x, y, width, height = [float(value) for value in selected[:4]]
        landmarks = np.asarray(selected[4:14]).reshape(5, 2)
        observation = {
            "track_id": int(track["track_id"]),
            "frame_id": int(frame_id),
            "detected_at_unix": time.time(),
            "last_seen_monotonic": float(now_monotonic),
            "bbox": {
                "x": _clip(x / frame_width),
                "y": _clip(y / frame_height),
                "width": _clip(width / frame_width),
                "height": _clip(height / frame_height),
            },
            "landmarks": [
                {
                    "x": _clip(float(point[0]) / frame_width),
                    "y": _clip(float(point[1]) / frame_height),
                }
                for point in landmarks
            ],
            "quality": quality,
            "stable_frames": 0,
            "ready": False,
            "primary": bool(track.get("primary")),
        }
        return observation, selected

    def expire(self, tracks: list[dict[str, Any]], now_monotonic: float) -> None:
        active_ids = {
            int(track["track_id"])
            for track in tracks
            if track.get("state") != "removed"
        }
        with self._lock:
            for track_id in list(self._faces):
                observation = self._faces[track_id]
                if (
                    track_id not in active_ids
                    or now_monotonic - float(observation["last_seen_monotonic"])
                    > self.settings.stale_seconds
                ):
                    self._remove_track(track_id)

    def _remove_track(self, track_id: int) -> None:
        self._faces.pop(track_id, None)
        self._face_rows.pop(track_id, None)
        self._ready_state.pop(track_id, None)

    def face_row(self, track_id: int) -> np.ndarray | None:
        with self._lock:
            row = self._face_rows.get(int(track_id))
            return None if row is None else row.copy()

    def face_observation(self, track_id: int) -> dict[str, Any] | None:
        with self._lock:
            observation = self._faces.get(int(track_id))
            return None if observation is None else self._public_face(observation)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            faces = [self._public_face(item) for item in self._faces.values()]
        faces.sort(key=lambda item: item["track_id"])
        return {
            "enabled": self.settings.enabled,
            "ready": self.ready,
            "model_path": str(self.model_path),
            "model_exists": self.model_path.exists(),
            "detector_max_side": MAX_DETECTOR_SIDE,
            "detect_count": self.detect_count,
            "last_detection_ms": self.last_detection_ms,
            "face_count": len(faces),
            "ready_face_count": sum(1 for item in faces if item["ready"]),
            "faces": faces,
            "last_error": self.last_error,
        }

    @staticmethod
    def _public_face(observation: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in observation.items()
            if key != "last_seen_monotonic"
        }
