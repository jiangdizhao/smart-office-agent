from __future__ import annotations

import itertools
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from app.appearance import AppearanceExtractor, cosine_similarity
from app.config import PresenceSettings, PrimarySettings, TrackingSettings

INF = 1_000_000.0


def bbox_to_xywh(box: dict[str, float]) -> np.ndarray:
    return np.asarray(
        [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, box["width"], box["height"]],
        dtype=np.float64,
    )


def xywh_to_bbox(values: Iterable[float]) -> dict[str, float]:
    cx, cy, width, height = [float(value) for value in values]
    width = float(np.clip(width, 1e-4, 1.0))
    height = float(np.clip(height, 1e-4, 1.0))
    x = float(np.clip(cx - width / 2, 0.0, max(0.0, 1.0 - width)))
    y = float(np.clip(cy - height / 2, 0.0, max(0.0, 1.0 - height)))
    return {"x": x, "y": y, "width": width, "height": height}


def bbox_iou(left: dict[str, float], right: dict[str, float]) -> float:
    x1 = max(left["x"], right["x"])
    y1 = max(left["y"], right["y"])
    x2 = min(left["x"] + left["width"], right["x"] + right["width"])
    y2 = min(left["y"] + left["height"], right["y"] + right["height"])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = left["width"] * left["height"] + right["width"] * right["height"] - intersection
    return intersection / union if union > 1e-12 else 0.0


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            cross_x = x1 + (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12)
            if x < cross_x:
                inside = not inside
        previous = current
    return inside


def hungarian_assign(costs: np.ndarray, max_cost: float) -> list[tuple[int, int]]:
    """O(n^3) rectangular Hungarian assignment with no SciPy dependency."""
    original = np.asarray(costs, dtype=np.float64)
    if original.size == 0:
        return []
    matrix = original
    transposed = False
    if matrix.shape[0] > matrix.shape[1]:
        matrix = matrix.T
        transposed = True
    matrix = np.where(np.isfinite(matrix), matrix, INF)
    rows, columns = matrix.shape
    u = np.zeros(rows + 1)
    v = np.zeros(columns + 1)
    p = np.zeros(columns + 1, dtype=np.int64)
    way = np.zeros(columns + 1, dtype=np.int64)
    for row in range(1, rows + 1):
        p[0] = row
        min_value = np.full(columns + 1, INF)
        used = np.zeros(columns + 1, dtype=bool)
        column0 = 0
        while True:
            used[column0] = True
            row0 = p[column0]
            delta = INF
            column1 = 0
            for column in range(1, columns + 1):
                if used[column]:
                    continue
                current = matrix[row0 - 1, column - 1] - u[row0] - v[column]
                if current < min_value[column]:
                    min_value[column] = current
                    way[column] = column0
                if min_value[column] < delta:
                    delta = min_value[column]
                    column1 = column
            for column in range(columns + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    min_value[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = way[column0]
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break
    matches: list[tuple[int, int]] = []
    for column in range(1, columns + 1):
        if p[column] == 0:
            continue
        pair = (column - 1, p[column] - 1) if transposed else (p[column] - 1, column - 1)
        if original[pair] <= max_cost:
            matches.append((int(pair[0]), int(pair[1])))
    return matches


class KalmanXYWH:
    def __init__(self, measurement: np.ndarray) -> None:
        self.mean = np.zeros(8, dtype=np.float64)
        self.mean[:4] = measurement
        self.covariance = np.diag([0.01, 0.01, 0.02, 0.02, 0.08, 0.08, 0.08, 0.08]) ** 2
        self.measurement_matrix = np.eye(4, 8)

    def predict(self, dt: float) -> None:
        dt = float(np.clip(dt, 1 / 60, 1.0))
        transition = np.eye(8)
        transition[:4, 4:] = np.eye(4) * dt
        scale = max(float(self.mean[2]), float(self.mean[3]), 0.02)
        process = np.diag([0.02 * scale] * 4 + [0.08 * scale] * 4) ** 2
        self.mean = transition @ self.mean
        self.covariance = transition @ self.covariance @ transition.T + process

    def update(self, measurement: np.ndarray) -> None:
        scale = max(float(measurement[2]), float(measurement[3]), 0.02)
        noise = np.diag([0.03 * scale, 0.03 * scale, 0.06 * scale, 0.06 * scale]) ** 2
        projected_mean = self.measurement_matrix @ self.mean
        projected_covariance = self.measurement_matrix @ self.covariance @ self.measurement_matrix.T + noise
        gain = self.covariance @ self.measurement_matrix.T @ np.linalg.inv(projected_covariance)
        self.mean += gain @ (measurement - projected_mean)
        self.covariance -= gain @ projected_covariance @ gain.T

    def gating_distance(self, measurement: np.ndarray) -> float:
        projected_mean = self.measurement_matrix @ self.mean
        projected_covariance = self.measurement_matrix @ self.covariance @ self.measurement_matrix.T
        residual = measurement - projected_mean
        return float(residual.T @ np.linalg.inv(projected_covariance + np.eye(4) * 1e-5) @ residual)

    @property
    def bbox(self) -> dict[str, float]:
        return xywh_to_bbox(self.mean[:4])


@dataclass
class Track:
    track_id: int
    kalman: KalmanXYWH
    score: float
    created_at: float
    last_seen_at: float
    last_prediction_at: float
    state: str = "tentative"
    hits: int = 1
    age_frames: int = 1
    misses: int = 0
    visible: bool = True
    engaged: bool = False
    engagement_candidate_frames: int = 0
    recovered_count: int = 0
    last_match_kind: str = "new"
    feature: np.ndarray | None = None
    gallery: deque[np.ndarray] = field(default_factory=deque)
    trail: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=30))
    area_history: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=20))
    removed_at: float | None = None

    @classmethod
    def create(cls, track_id: int, detection: dict[str, Any], now: float, gallery_size: int) -> Track:
        track = cls(
            track_id=track_id,
            kalman=KalmanXYWH(bbox_to_xywh(detection["bbox"])),
            score=float(detection["score"]),
            created_at=now,
            last_seen_at=now,
            last_prediction_at=now,
            gallery=deque(maxlen=gallery_size),
        )
        track.append_history(now)
        return track

    @property
    def bbox(self) -> dict[str, float]:
        return self.kalman.bbox

    @property
    def area_ratio(self) -> float:
        box = self.bbox
        return box["width"] * box["height"]

    @property
    def bottom_center(self) -> tuple[float, float]:
        box = self.bbox
        return box["x"] + box["width"] / 2, box["y"] + box["height"]

    def predict(self, now: float) -> None:
        self.kalman.predict(max(now - self.last_prediction_at, 1e-3))
        self.last_prediction_at = now
        self.age_frames += 1
        self.visible = False

    def update(
        self,
        detection: dict[str, Any],
        now: float,
        feature: np.ndarray | None,
        match_kind: str,
        feature_alpha: float,
    ) -> tuple[bool, float]:
        was_lost = self.state == "lost"
        lost_seconds = max(now - self.last_seen_at, 0.0) if was_lost else 0.0
        self.kalman.update(bbox_to_xywh(detection["bbox"]))
        self.score = float(detection["score"])
        self.last_seen_at = now
        self.last_prediction_at = now
        self.hits += 1
        self.misses = 0
        self.visible = True
        self.last_match_kind = match_kind
        if was_lost:
            self.state = "confirmed"
            self.recovered_count += 1
        if feature is not None:
            if self.feature is None or self.feature.size != feature.size:
                self.feature = feature.copy()
            else:
                combined = (1 - feature_alpha) * self.feature + feature_alpha * feature
                norm = float(np.linalg.norm(combined))
                self.feature = (combined / norm).astype(np.float32) if norm > 1e-12 else combined
            self.gallery.append(feature.copy())
        self.append_history(now)
        return was_lost, lost_seconds

    def append_history(self, now: float) -> None:
        self.trail.append(self.bottom_center)
        self.area_history.append((now, self.area_ratio))

    def approaching_score(self) -> float:
        if len(self.area_history) < 3:
            return 0.5
        first_time, first_area = self.area_history[0]
        last_time, last_area = self.area_history[-1]
        rate = (last_area - first_area) / max(last_time - first_time, 1e-3)
        return float(np.clip(0.5 + 4 * rate, 0.0, 1.0))

    def snapshot(self, now: float, primary: bool) -> dict[str, Any]:
        box = self.bbox
        return {
            "track_id": self.track_id,
            "state": self.state,
            "visible": self.visible,
            "primary": primary,
            "score": round(self.score, 6),
            "bbox": {key: round(float(value), 6) for key, value in box.items()},
            "area_ratio": round(self.area_ratio, 6),
            "bottom_center": {"x": round(self.bottom_center[0], 6), "y": round(self.bottom_center[1], 6)},
            "velocity": {
                "x": round(float(self.kalman.mean[4]), 6),
                "y": round(float(self.kalman.mean[5]), 6),
                "width": round(float(self.kalman.mean[6]), 6),
                "height": round(float(self.kalman.mean[7]), 6),
            },
            "hits": self.hits,
            "age_frames": self.age_frames,
            "misses": self.misses,
            "dwell_seconds": round(max(now - self.created_at, 0.0), 3),
            "lost_seconds": round(max(now - self.last_seen_at, 0.0), 3) if self.state == "lost" else 0.0,
            "engaged": self.engaged,
            "recovered_count": self.recovered_count,
            "last_match_kind": self.last_match_kind,
            "appearance_gallery_size": len(self.gallery),
            "trail": [[round(x, 6), round(y, 6)] for x, y in self.trail],
        }


class PrimarySelector:
    def __init__(self, settings: PrimarySettings, presence: PresenceSettings) -> None:
        self.settings = settings
        self.presence = presence
        self.primary_track_id: int | None = None
        self.candidate_id: int | None = None
        self.candidate_since: float | None = None
        self.challenger_id: int | None = None
        self.challenger_since: float | None = None
        self.scores: dict[int, float] = {}

    def update(self, tracks: list[Track], now: float) -> tuple[int | None, dict[str, Any] | None]:
        self.scores = {track.track_id: self.score(track, now) for track in tracks}
        by_id = {track.track_id: track for track in tracks}
        previous = self.primary_track_id
        current = by_id.get(previous) if previous is not None else None
        if current is not None and not current.visible and now - current.last_seen_at <= self.settings.lost_lock_seconds:
            return previous, None
        if current is not None and not current.visible:
            current = None
        ranked = sorted(tracks, key=lambda track: self.scores[track.track_id], reverse=True)
        best = ranked[0] if ranked else None
        if current is None:
            if best is None:
                self.primary_track_id = None
                self.candidate_id = None
                self.candidate_since = None
            elif self.candidate_id != best.track_id:
                self.candidate_id = best.track_id
                self.candidate_since = now
            elif now - float(self.candidate_since or now) >= self.settings.acquire_stable_seconds:
                self.primary_track_id = best.track_id
                self.candidate_id = None
                self.candidate_since = None
        elif best is not None and best.track_id != current.track_id:
            if self.scores[best.track_id] - self.scores[current.track_id] >= self.settings.challenger_margin:
                if self.challenger_id != best.track_id:
                    self.challenger_id = best.track_id
                    self.challenger_since = now
                elif now - float(self.challenger_since or now) >= self.settings.challenger_hold_seconds:
                    self.primary_track_id = best.track_id
                    self.challenger_id = None
                    self.challenger_since = None
            else:
                self.challenger_id = None
                self.challenger_since = None
        else:
            self.challenger_id = None
            self.challenger_since = None
        if previous == self.primary_track_id:
            return self.primary_track_id, None
        return self.primary_track_id, {
            "previous_track_id": previous,
            "current_track_id": self.primary_track_id,
            "reason": "acquired" if previous is None else "previous_lost_or_challenged",
            "previous_score": self.scores.get(previous),
            "current_score": self.scores.get(self.primary_track_id),
        }

    def score(self, track: Track, now: float) -> float:
        x, y = track.bottom_center
        zone = 1.0 if point_in_polygon((x, y), self.presence.engagement_zone) else 0.0
        area = float(np.clip(track.area_ratio / self.settings.area_full_scale_ratio, 0.0, 1.0))
        centrality = float(np.clip(1 - math.hypot(x - self.settings.target_x, y - self.settings.target_y) / math.sqrt(2), 0, 1))
        dwell = float(np.clip((now - track.created_at) / self.settings.dwell_full_scale_seconds, 0, 1))
        stability = float(np.clip(track.hits / self.settings.stability_full_scale_hits, 0, 1))
        return round(0.30 * zone + 0.25 * area + 0.15 * centrality + 0.15 * dwell + 0.10 * track.approaching_score() + 0.05 * stability, 6)


class MultiObjectTracker:
    def __init__(self, settings: TrackingSettings, presence: PresenceSettings, primary: PrimarySettings, appearance: AppearanceExtractor) -> None:
        self.settings = settings
        self.presence = presence
        self.appearance = appearance
        self.primary = PrimarySelector(primary, presence)
        self.tracks: list[Track] = []
        self.next_id = itertools.count(1)
        self.group_active = False
        self.update_count = 0
        self.last_update_ms: float | None = None
        self.last_association: dict[str, Any] = {}

    def reset(self) -> None:
        self.tracks.clear()
        self.next_id = itertools.count(1)
        self.group_active = False
        self.primary.primary_track_id = None

    def update(self, detections: list[dict[str, Any]], source_frame: np.ndarray, now: float | None = None) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
        started = time.perf_counter()
        now = time.monotonic() if now is None else now
        events: list[tuple[str, dict[str, Any]]] = []
        self.update_count += 1
        active = [track for track in self.tracks if track.state != "removed"]
        for track in active:
            track.predict(now)
        high = [i for i, detection in enumerate(detections) if detection["score"] >= self.settings.high_threshold]
        low = [i for i, detection in enumerate(detections) if self.settings.low_threshold <= detection["score"] < self.settings.high_threshold]
        features = {i: self.appearance.extract(source_frame, detections[i]["bbox"]) for i in high}

        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        stage1_tracks = [track for track in active if track.state in {"confirmed", "lost"}]
        stage1_pairs = self.associate(stage1_tracks, high, detections, features, True, self.settings.first_stage_max_cost)
        recovered_ids: list[int] = []
        for track_row, detection_column in stage1_pairs:
            track = stage1_tracks[track_row]
            detection_index = high[detection_column]
            recovered, lost_seconds = track.update(detections[detection_index], now, features.get(detection_index), "high", self.settings.feature_ema_alpha)
            matched_tracks.add(track.track_id)
            matched_detections.add(detection_index)
            if recovered:
                recovered_ids.append(track.track_id)
                events.append(("track_recovered", {"track_id": track.track_id, "lost_seconds": round(lost_seconds, 3)}))

        stage2_tracks = [track for track in active if track.state == "confirmed" and track.track_id not in matched_tracks]
        remaining_low = [index for index in low if index not in matched_detections]
        stage2_pairs = self.associate(stage2_tracks, remaining_low, detections, {}, False, self.settings.second_stage_max_cost)
        for track_row, detection_column in stage2_pairs:
            track = stage2_tracks[track_row]
            detection_index = remaining_low[detection_column]
            track.update(detections[detection_index], now, None, "low", self.settings.feature_ema_alpha)
            matched_tracks.add(track.track_id)
            matched_detections.add(detection_index)

        tentative_tracks = [track for track in active if track.state == "tentative" and track.track_id not in matched_tracks]
        remaining_high = [index for index in high if index not in matched_detections]
        tentative_pairs = self.associate(tentative_tracks, remaining_high, detections, features, True, self.settings.tentative_max_cost)
        for track_row, detection_column in tentative_pairs:
            track = tentative_tracks[track_row]
            detection_index = remaining_high[detection_column]
            track.update(detections[detection_index], now, features.get(detection_index), "tentative", self.settings.feature_ema_alpha)
            matched_tracks.add(track.track_id)
            matched_detections.add(detection_index)
            if track.hits >= self.settings.confirm_hits and track.age_frames <= self.settings.confirm_window_frames:
                track.state = "confirmed"
                events.append(("visitor_entered", self.event_payload(track, now)))

        for track in active:
            if track.track_id in matched_tracks:
                continue
            track.misses += 1
            if track.state == "tentative" and track.age_frames >= self.settings.confirm_window_frames:
                track.state = "removed"
                track.removed_at = now
            elif track.state == "confirmed":
                track.state = "lost"
            elif track.state == "lost" and now - track.last_seen_at > self.settings.lost_timeout_seconds:
                track.state = "removed"
                track.removed_at = now
                events.append(("visitor_left", self.event_payload(track, now)))

        for detection_index in high:
            if detection_index in matched_detections or detections[detection_index]["score"] < self.settings.new_track_threshold:
                continue
            track = Track.create(next(self.next_id), detections[detection_index], now, self.settings.gallery_size)
            feature = features.get(detection_index)
            if feature is not None:
                track.feature = feature.copy()
                track.gallery.append(feature.copy())
            self.tracks.append(track)
            if self.settings.confirm_hits == 1:
                track.state = "confirmed"
                events.append(("visitor_entered", self.event_payload(track, now)))

        self.tracks = [track for track in self.tracks if track.state != "removed" or now - float(track.removed_at or now) <= self.settings.removed_retention_seconds]
        confirmed = [track for track in self.tracks if track.state == "confirmed"]
        for track in confirmed:
            in_zone = point_in_polygon(track.bottom_center, self.presence.engagement_zone)
            if track.visible and in_zone and track.area_ratio >= self.presence.engagement_min_area_ratio:
                track.engagement_candidate_frames += 1
                if not track.engaged and track.engagement_candidate_frames >= self.presence.engage_confirm_frames:
                    track.engaged = True
                    events.append(("visitor_engaged", self.event_payload(track, now)))
            elif track.visible:
                track.engagement_candidate_frames = 0

        visible_confirmed = [track for track in confirmed if track.visible]
        group_now = len(visible_confirmed) >= 2
        if group_now and not self.group_active:
            events.append(("group_detected", {"person_count": len(visible_confirmed), "track_ids": [track.track_id for track in visible_confirmed]}))
        self.group_active = group_now
        primary_id, primary_event = self.primary.update(confirmed, now)
        if primary_event is not None:
            events.append(("primary_visitor_changed", primary_event))

        self.last_association = {
            "high_detections": len(high),
            "low_detections": len(low),
            "stage1_matches": len(stage1_pairs),
            "stage2_matches": len(stage2_pairs),
            "tentative_matches": len(tentative_pairs),
            "recovered_track_ids": recovered_ids,
        }
        self.last_update_ms = round((time.perf_counter() - started) * 1000, 3)
        return [track.snapshot(now, track.track_id == primary_id) for track in self.tracks if track.state != "removed"], events

    def associate(self, tracks: list[Track], detection_indices: list[int], detections: list[dict[str, Any]], features: dict[int, np.ndarray | None], use_appearance: bool, max_cost: float) -> list[tuple[int, int]]:
        if not tracks or not detection_indices:
            return []
        costs = np.full((len(tracks), len(detection_indices)), INF, dtype=np.float64)
        for row, track in enumerate(tracks):
            predicted = track.bbox
            predicted_xywh = bbox_to_xywh(predicted)
            for column, detection_index in enumerate(detection_indices):
                observed = detections[detection_index]["bbox"]
                observed_xywh = bbox_to_xywh(observed)
                iou = bbox_iou(predicted, observed)
                center_distance = float(np.linalg.norm(predicted_xywh[:2] - observed_xywh[:2]))
                allowed_center = self.settings.lost_max_center_distance if track.state == "lost" else self.settings.max_center_distance
                if center_distance > allowed_center and iou < self.settings.minimum_iou_gate:
                    continue
                if track.kalman.gating_distance(observed_xywh) > self.settings.mahalanobis_gate and iou < 0.05:
                    continue
                appearance_distance = 0.5
                if use_appearance:
                    similarity = cosine_similarity(track.feature, features.get(detection_index))
                    if similarity is not None:
                        if similarity < self.settings.appearance_similarity_gate and iou < 0.10:
                            continue
                        appearance_distance = 1 - similarity
                cost = self.settings.iou_weight * (1 - iou) + self.settings.motion_weight * min(center_distance / allowed_center, 1.0)
                if use_appearance:
                    cost += self.settings.appearance_weight * appearance_distance
                else:
                    cost /= max(self.settings.iou_weight + self.settings.motion_weight, 1e-9)
                costs[row, column] = cost
        return hungarian_assign(costs, max_cost)

    def event_payload(self, track: Track, now: float) -> dict[str, Any]:
        payload = track.snapshot(now, track.track_id == self.primary.primary_track_id)
        payload["visit_session_id"] = f"visit_{track.track_id}_{int(track.created_at * 1000)}"
        return payload

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        primary_id = self.primary.primary_track_id
        tracks = [track.snapshot(now, track.track_id == primary_id) for track in self.tracks if track.state != "removed"]
        visible = [track for track in tracks if track["visible"] and track["state"] == "confirmed"]
        primary = next((track for track in tracks if track["primary"]), None)
        scene_state = "engaged" if primary and primary["engaged"] else "present" if visible else "absent"
        return {
            "scene_state": scene_state,
            "person_count": len(visible),
            "track_count": len(tracks),
            "primary_track_id": primary_id,
            "primary_scores": dict(self.primary.scores),
            "tracks": tracks,
            "update_count": self.update_count,
            "last_update_ms": self.last_update_ms,
            "last_association": dict(self.last_association),
            "appearance": self.appearance.status(),
        }
