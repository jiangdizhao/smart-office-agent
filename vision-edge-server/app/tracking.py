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


INF_COST = 1_000_000.0


def bbox_to_xywh(bbox: dict[str, float]) -> np.ndarray:
    return np.asarray(
        [
            bbox["x"] + bbox["width"] / 2.0,
            bbox["y"] + bbox["height"] / 2.0,
            bbox["width"],
            bbox["height"],
        ],
        dtype=np.float64,
    )


def xywh_to_bbox(values: Iterable[float]) -> dict[str, float]:
    cx, cy, width, height = (float(item) for item in values)
    width = float(np.clip(width, 1e-4, 1.5))
    height = float(np.clip(height, 1e-4, 1.5))
    return {
        "x": float(np.clip(cx - width / 2.0, 0.0, 1.0)),
        "y": float(np.clip(cy - height / 2.0, 0.0, 1.0)),
        "width": float(np.clip(width, 1e-4, 1.0)),
        "height": float(np.clip(height, 1e-4, 1.0)),
    }


def bbox_iou(left: dict[str, float], right: dict[str, float]) -> float:
    left_x2 = left["x"] + left["width"]
    left_y2 = left["y"] + left["height"]
    right_x2 = right["x"] + right["width"]
    right_y2 = right["y"] + right["height"]
    inter_width = max(0.0, min(left_x2, right_x2) - max(left["x"], right["x"]))
    inter_height = max(0.0, min(left_y2, right_y2) - max(left["y"], right["y"]))
    intersection = inter_width * inter_height
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
            crossing_x = (x2 - x1) * (y - y1) / max(y2 - y1, 1e-12) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def hungarian_assign(cost_matrix: np.ndarray, max_cost: float) -> list[tuple[int, int]]:
    """Rectangular minimum-cost assignment without a SciPy dependency."""

    if cost_matrix.size == 0:
        return []
    original = np.asarray(cost_matrix, dtype=np.float64)
    transposed = False
    matrix = original
    if matrix.shape[0] > matrix.shape[1]:
        matrix = matrix.T
        transposed = True
    n_rows, n_cols = matrix.shape
    finite = np.where(np.isfinite(matrix), matrix, INF_COST)

    # Shortest augmenting path Hungarian algorithm, 1-indexed.
    u = np.zeros(n_rows + 1, dtype=np.float64)
    v = np.zeros(n_cols + 1, dtype=np.float64)
    p = np.zeros(n_cols + 1, dtype=np.int64)
    way = np.zeros(n_cols + 1, dtype=np.int64)
    for row in range(1, n_rows + 1):
        p[0] = row
        min_values = np.full(n_cols + 1, INF_COST, dtype=np.float64)
        used = np.zeros(n_cols + 1, dtype=bool)
        column0 = 0
        while True:
            used[column0] = True
            row0 = p[column0]
            delta = INF_COST
            column1 = 0
            for column in range(1, n_cols + 1):
                if used[column]:
                    continue
                current = finite[row0 - 1, column - 1] - u[row0] - v[column]
                if current < min_values[column]:
                    min_values[column] = current
                    way[column] = column0
                if min_values[column] < delta:
                    delta = min_values[column]
                    column1 = column
            for column in range(n_cols + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    min_values[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = way[column0]
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break

    pairs: list[tuple[int, int]] = []
    for column in range(1, n_cols + 1):
        if p[column] == 0:
            continue
        row = int(p[column] - 1)
        col = int(column - 1)
        original_pair = (col, row) if transposed else (row, col)
        if original[original_pair] <= max_cost:
            pairs.append(original_pair)
    return pairs


class KalmanXYWH:
    def __init__(self, measurement: np.ndarray) -> None:
        self.mean = np.zeros(8, dtype=np.float64)
        self.mean[:4] = measurement
        self.covariance = np.diag([0.01, 0.01, 0.02, 0.02, 0.1, 0.1, 0.1, 0.1]) ** 2
        self._measurement = np.eye(4, 8, dtype=np.float64)

    def predict(self, dt: float) -> None:
        dt = float(np.clip(dt, 1.0 / 60.0, 1.0))
        transition = np.eye(8, dtype=np.float64)
        transition[:4, 4:] = np.eye(4) * dt
        scale = max(self.mean[2], self.mean[3], 0.02)
        position_noise = 0.02 * scale
        velocity_noise = 0.08 * scale
        process = np.diag(
            [position_noise] * 4 + [velocity_noise] * 4
        ) ** 2
        self.mean = transition @ self.mean
        self.covariance = transition @ self.covariance @ transition.T + process

    def update(self, measurement: np.ndarray) -> None:
        scale = max(measurement[2], measurement[3], 0.02)
        noise = np.diag([0.03 * scale, 0.03 * scale, 0.06 * scale, 0.06 * scale]) ** 2
        projected_mean = self._measurement @ self.mean
        projected_cov = self._measurement @ self.covariance @ self._measurement.T + noise
        gain = self.covariance @ self._measurement.T @ np.linalg.inv(projected_cov)
        residual = measurement - projected_mean
        self.mean = self.mean + gain @ residual
        self.covariance = self.covariance - gain @ projected_cov @ gain.T

    def gating_distance(self, measurement: np.ndarray) -> float:
        projected_mean = self._measurement @ self.mean
        projected_cov = self._measurement @ self.covariance @ self._measurement.T
        projected_cov += np.eye(4) * 1e-5
        residual = measurement - projected_mean
        return float(residual.T @ np.linalg.inv(projected_cov) @ residual)

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
    gallery: deque[np.ndarray] = field(default_factory=lambda: deque(maxlen=10))
    trail: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=30))
    area_history: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=20))
    last_feature_at: float = 0.0
    last_prediction_at: float = 0.0
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
        )
        track.gallery = deque(maxlen=gallery_size)
        track._append_history(now)
        return track

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
        *,
        match_kind: str,
        feature_alpha: float,
    ) -> bool:
        recovered = self.state == "lost"
        self.kalman.update(bbox_to_xywh(detection["bbox"]))
        self.score = float(detection["score"])
        self.last_seen_at = now
        self.last_prediction_at = now
        self.hits += 1
        self.misses = 0
        self.visible = True
        self.last_match_kind = match_kind
        if recovered:
            self.recovered_count += 1
            self.state = "confirmed"
        if feature is not None:
            if self.feature is None or self.feature.size != feature.size:
                self.feature = feature.copy()
            else:
                combined = (1.0 - feature_alpha) * self.feature + feature_alpha * feature
                norm = float(np.linalg.norm(combined))
                self.feature = (combined / norm).astype(np.float32) if norm > 1e-12 else combined
            self.gallery.append(feature.copy())
            self.last_feature_at = now
        self._append_history(now)
        return recovered

    def _append_history(self, now: float) -> None:
        bbox = self.kalman.bbox
        bottom_center = (bbox["x"] + bbox["width"] / 2.0, bbox["y"] + bbox["height"])
        self.trail.append(bottom_center)
        self.area_history.append((now, bbox["width"] * bbox["height"]))

    @property
    def bbox(self) -> dict[str, float]:
        return self.kalman.bbox

    @property
    def area_ratio(self) -> float:
        bbox = self.bbox
        return bbox["width"] * bbox["height"]

    @property
    def bottom_center(self) -> tuple[float, float]:
        bbox = self.bbox
        return bbox["x"] + bbox["width"] / 2.0, bbox["y"] + bbox["height"]

    def approaching_score(self) -> float:
        if len(self.area_history) < 3:
            return 0.5
        first_time, first_area = self.area_history[0]
        last_time, last_area = self.area_history[-1]
        elapsed = max(last_time - first_time, 1e-3)
        rate = (last_area - first_area) / elapsed
        return float(np.clip(0.5 + rate * 4.0, 0.0, 1.0))

    def snapshot(self, now: float, primary: bool = False) -> dict[str, Any]:
        bbox = self.bbox
        return {
            "track_id": self.track_id,
            "state": self.state,
            "visible": self.visible,
            "primary": primary,
            "score": round(self.score, 6),
            "bbox": {key: round(float(value), 6) for key, value in bbox.items()},
            "area_ratio": round(self.area_ratio, 6),
            "bottom_center": {
                "x": round(self.bottom_center[0], 6),
                "y": round(self.bottom_center[1], 6),
            },
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
        self._candidate_id: int | None = None
        self._candidate_since: float | None = None
        self._challenger_id: int | None = None
        self._challenger_since: float | None = None
        self.scores: dict[int, float] = {}

    def update(self, tracks: list[Track], now: float) -> tuple[int | None, dict[str, Any] | None]:
        eligible = [track for track in tracks if track.state == "confirmed"]
        self.scores = {track.track_id: self._score(track, now) for track in eligible}
        by_id = {track.track_id: track for track in eligible}
        previous = self.primary_track_id

        current = by_id.get(previous) if previous is not None else None
        if current is not None and not current.visible:
            lost_age = now - current.last_seen_at
            if lost_age <= self.settings.lost_lock_seconds:
                return previous, None
            current = None

        ranked = sorted(eligible, key=lambda item: self.scores[item.track_id], reverse=True)
        best = ranked[0] if ranked else None
        if current is None:
            if best is None:
                self.primary_track_id = None
                self._candidate_id = None
                self._candidate_since = None
            elif self._candidate_id != best.track_id:
                self._candidate_id = best.track_id
                self._candidate_since = now
            elif now - float(self._candidate_since or now) >= self.settings.acquire_stable_seconds:
                self.primary_track_id = best.track_id
                self._candidate_id = None
                self._candidate_since = None
        elif best is not None and best.track_id != current.track_id:
            margin = self.scores[best.track_id] - self.scores[current.track_id]
            if margin >= self.settings.challenger_margin:
                if self._challenger_id != best.track_id:
                    self._challenger_id = best.track_id
                    self._challenger_since = now
                elif now - float(self._challenger_since or now) >= self.settings.challenger_hold_seconds:
                    self.primary_track_id = best.track_id
                    self._challenger_id = None
                    self._challenger_since = None
            else:
                self._challenger_id = None
                self._challenger_since = None
        else:
            self._challenger_id = None
            self._challenger_since = None

        if self.primary_track_id == previous:
            return self.primary_track_id, None
        reason = "acquired" if previous is None else "previous_lost_or_challenged"
        return self.primary_track_id, {
            "previous_track_id": previous,
            "current_track_id": self.primary_track_id,
            "reason": reason,
            "previous_score": self.scores.get(previous) if previous is not None else None,
            "current_score": self.scores.get(self.primary_track_id)
            if self.primary_track_id is not None
            else None,
        }

    def _score(self, track: Track, now: float) -> float:
        x, y = track.bottom_center
        zone = 1.0 if point_in_polygon((x, y), self.presence.engagement_zone) else 0.0
        area = float(np.clip(track.area_ratio / self.settings.area_full_scale_ratio, 0.0, 1.0))
        center_distance = math.hypot(x - self.settings.target_x, y - self.settings.target_y)
        centrality = float(np.clip(1.0 - center_distance / math.sqrt(2.0), 0.0, 1.0))
        dwell = float(np.clip((now - track.created_at) / self.settings.dwell_full_scale_seconds, 0.0, 1.0))
        approach = track.approaching_score()
        stability = float(np.clip(track.hits / self.settings.stability_full_scale_hits, 0.0, 1.0))
        return round(
            0.30 * zone
            + 0.25 * area
            + 0.15 * centrality
            + 0.15 * dwell
            + 0.10 * approach
            + 0.05 * stability,
            6,
        )


class MultiObjectTracker:
    def __init__(
        self,
        settings: TrackingSettings,
        presence: PresenceSettings,
        primary_settings: PrimarySettings,
        appearance: AppearanceExtractor,
    ) -> None:
        self.settings = settings
        self.presence = presence
        self.appearance = appearance
        self.primary_selector = PrimarySelector(primary_settings, presence)
        self.tracks: list[Track] = []
        self._next_track_id = itertools.count(1)
        self._group_active = False
        self.update_count = 0
        self.id_switch_suspicions = 0
        self.last_update_ms: float | None = None
        self.last_association: dict[str, Any] = {}

    def reset(self) -> None:
        self.tracks.clear()
        self._next_track_id = itertools.count(1)
        self._group_active = False
        self.primary_selector.primary_track_id = None

    def update(
        self,
        detections: list[dict[str, Any]],
        source_frame: np.ndarray,
        *,
        now: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
        started = time.perf_counter()
        now = time.monotonic() if now is None else now
        events: list[tuple[str, dict[str, Any]]] = []
        self.update_count += 1

        active_tracks = [track for track in self.tracks if track.state != "removed"]
        for track in active_tracks:
            track.predict(now)

        high_indices = [
            index for index, item in enumerate(detections) if item["score"] >= self.settings.high_threshold
        ]
        low_indices = [
            index
            for index, item in enumerate(detections)
            if self.settings.low_threshold <= item["score"] < self.settings.high_threshold
        ]
        features: dict[int, np.ndarray | None] = {}
        for index in high_indices:
            features[index] = self.appearance.extract(source_frame, detections[index]["bbox"])

        confirmed_or_lost = [track for track in active_tracks if track.state in {"confirmed", "lost"}]
        stage1 = self._associate(
            confirmed_or_lost,
            high_indices,
            detections,
            features,
            use_appearance=True,
            max_cost=self.settings.first_stage_max_cost,
        )
        matched_track_ids: set[int] = set()
        matched_detection_indices: set[int] = set()
        recovered_ids: list[int] = []
        for track_index, detection_index in stage1:
            track = confirmed_or_lost[track_index]
            recovered = track.update(
                detections[detection_index],
                now,
                features.get(detection_index),
                match_kind="high",
                feature_alpha=self.settings.feature_ema_alpha,
            )
            matched_track_ids.add(track.track_id)
            matched_detection_indices.add(detection_index)
            if recovered:
                recovered_ids.append(track.track_id)
                events.append(
                    (
                        "track_recovered",
                        {
                            "track_id": track.track_id,
                            "lost_seconds": round(now - track.last_seen_at, 3),
                        },
                    )
                )

        unmatched_confirmed = [
            track
            for track in active_tracks
            if track.state == "confirmed" and track.track_id not in matched_track_ids
        ]
        remaining_low = [index for index in low_indices if index not in matched_detection_indices]
        stage2 = self._associate(
            unmatched_confirmed,
            remaining_low,
            detections,
            {},
            use_appearance=False,
            max_cost=self.settings.second_stage_max_cost,
        )
        for track_index, local_detection_index in stage2:
            track = unmatched_confirmed[track_index]
            detection_index = remaining_low[local_detection_index]
            track.update(
                detections[detection_index],
                now,
                None,
                match_kind="low",
                feature_alpha=self.settings.feature_ema_alpha,
            )
            matched_track_ids.add(track.track_id)
            matched_detection_indices.add(detection_index)

        tentative = [
            track
            for track in active_tracks
            if track.state == "tentative" and track.track_id not in matched_track_ids
        ]
        remaining_high = [index for index in high_indices if index not in matched_detection_indices]
        tentative_matches = self._associate(
            tentative,
            remaining_high,
            detections,
            features,
            use_appearance=True,
            max_cost=self.settings.tentative_max_cost,
        )
        for track_index, local_detection_index in tentative_matches:
            track = tentative[track_index]
            detection_index = remaining_high[local_detection_index]
            track.update(
                detections[detection_index],
                now,
                features.get(detection_index),
                match_kind="tentative",
                feature_alpha=self.settings.feature_ema_alpha,
            )
            matched_track_ids.add(track.track_id)
            matched_detection_indices.add(detection_index)
            if track.hits >= self.settings.confirm_hits and track.age_frames <= self.settings.confirm_window_frames:
                track.state = "confirmed"
                events.append(("visitor_entered", self._event_payload(track, now)))

        for track in active_tracks:
            if track.track_id in matched_track_ids:
                continue
            track.misses += 1
            if track.state == "tentative":
                if track.age_frames >= self.settings.confirm_window_frames:
                    track.state = "removed"
                    track.removed_at = now
            elif track.state == "confirmed":
                track.state = "lost"
                track.visible = False
            elif track.state == "lost" and now - track.last_seen_at > self.settings.lost_timeout_seconds:
                track.state = "removed"
                track.removed_at = now
                events.append(("visitor_left", self._event_payload(track, now)))

        for detection_index in high_indices:
            if detection_index in matched_detection_indices:
                continue
            detection = detections[detection_index]
            if detection["score"] < self.settings.new_track_threshold:
                continue
            track = Track.create(
                next(self._next_track_id),
                detection,
                now,
                self.settings.gallery_size,
            )
            feature = features.get(detection_index)
            if feature is not None:
                track.feature = feature.copy()
                track.gallery.append(feature.copy())
                track.last_feature_at = now
            self.tracks.append(track)
            if self.settings.confirm_hits <= 1:
                track.state = "confirmed"
                events.append(("visitor_entered", self._event_payload(track, now)))

        # Remove long-dead objects from memory after retaining them briefly for diagnostics.
        self.tracks = [
            track
            for track in self.tracks
            if track.state != "removed"
            or now - float(track.removed_at or now) <= self.settings.removed_retention_seconds
        ]

        confirmed = [track for track in self.tracks if track.state == "confirmed"]
        for track in confirmed:
            in_zone = point_in_polygon(track.bottom_center, self.presence.engagement_zone)
            if track.visible and in_zone and track.area_ratio >= self.presence.engagement_min_area_ratio:
                track.engagement_candidate_frames += 1
                if not track.engaged and track.engagement_candidate_frames >= self.presence.engage_confirm_frames:
                    track.engaged = True
                    events.append(("visitor_engaged", self._event_payload(track, now)))
            elif track.visible:
                track.engagement_candidate_frames = 0

        visible_confirmed = [track for track in confirmed if track.visible]
        group_now = len(visible_confirmed) >= 2
        if group_now and not self._group_active:
            events.append(
                (
                    "group_detected",
                    {
                        "person_count": len(visible_confirmed),
                        "track_ids": [track.track_id for track in visible_confirmed],
                    },
                )
            )
        self._group_active = group_now

        primary_id, primary_event = self.primary_selector.update(confirmed, now)
        if primary_event is not None:
            events.append(("primary_visitor_changed", primary_event))

        self.last_association = {
            "high_detections": len(high_indices),
            "low_detections": len(low_indices),
            "stage1_matches": len(stage1),
            "stage2_matches": len(stage2),
            "tentative_matches": len(tentative_matches),
            "recovered_track_ids": recovered_ids,
        }
        self.last_update_ms = round((time.perf_counter() - started) * 1000.0, 3)
        snapshots = [
            track.snapshot(now, primary=track.track_id == primary_id)
            for track in self.tracks
            if track.state != "removed"
        ]
        return snapshots, events

    def _associate(
        self,
        tracks: list[Track],
        detection_indices: list[int],
        detections: list[dict[str, Any]],
        features: dict[int, np.ndarray | None],
        *,
        use_appearance: bool,
        max_cost: float,
    ) -> list[tuple[int, int]]:
        if not tracks or not detection_indices:
            return []
        matrix = np.full((len(tracks), len(detection_indices)), INF_COST, dtype=np.float64)
        for row, track in enumerate(tracks):
            predicted = track.bbox
            for column, detection_index in enumerate(detection_indices):
                detection = detections[detection_index]
                observed = detection["bbox"]
                iou = bbox_iou(predicted, observed)
                predicted_xywh = bbox_to_xywh(predicted)
                observed_xywh = bbox_to_xywh(observed)
                center_distance = float(np.linalg.norm(predicted_xywh[:2] - observed_xywh[:2]))
                gating = track.kalman.gating_distance(observed_xywh)
                allowed_center = (
                    self.settings.lost_max_center_distance
                    if track.state == "lost"
                    else self.settings.max_center_distance
                )
                if center_distance > allowed_center and iou < self.settings.minimum_iou_gate:
                    continue
                if gating > self.settings.mahalanobis_gate and iou < 0.05:
                    continue

                appearance_distance = 0.5
                if use_appearance:
                    similarity = cosine_similarity(track.feature, features.get(detection_index))
                    if similarity is not None:
                        if similarity < self.settings.appearance_similarity_gate and iou < 0.10:
                            continue
                        appearance_distance = 1.0 - similarity
                cost = (
                    self.settings.iou_weight * (1.0 - iou)
                    + self.settings.motion_weight * min(center_distance / allowed_center, 1.0)
                    + (self.settings.appearance_weight * appearance_distance if use_appearance else 0.0)
                )
                if not use_appearance:
                    denominator = self.settings.iou_weight + self.settings.motion_weight
                    cost /= max(denominator, 1e-9)
                matrix[row, column] = cost
        return hungarian_assign(matrix, max_cost=max_cost)

    def _event_payload(self, track: Track, now: float) -> dict[str, Any]:
        payload = track.snapshot(
            now,
            primary=track.track_id == self.primary_selector.primary_track_id,
        )
        payload["visit_session_id"] = f"visit_{track.track_id}_{int(track.created_at * 1000)}"
        return payload

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        primary_id = self.primary_selector.primary_track_id
        tracks = [
            track.snapshot(now, primary=track.track_id == primary_id)
            for track in self.tracks
            if track.state != "removed"
        ]
        visible = [track for track in tracks if track["visible"] and track["state"] == "confirmed"]
        primary = next((track for track in tracks if track["primary"]), None)
        if primary is not None and primary["engaged"]:
            scene_state = "engaged"
        elif visible:
            scene_state = "present"
        else:
            scene_state = "absent"
        return {
            "scene_state": scene_state,
            "person_count": len(visible),
            "track_count": len(tracks),
            "primary_track_id": primary_id,
            "primary_scores": dict(self.primary_selector.scores),
            "tracks": tracks,
            "update_count": self.update_count,
            "last_update_ms": self.last_update_ms,
            "last_association": dict(self.last_association),
            "appearance": self.appearance.status(),
            "id_switch_suspicions": self.id_switch_suspicions,
        }
