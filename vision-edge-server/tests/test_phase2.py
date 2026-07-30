from __future__ import annotations

import numpy as np

from app.config import PresenceSettings, PrimarySettings, TrackingSettings
from app.primary_lock import OcclusionAwarePrimarySelector
from app.tracking_runtime import (
    BLOCKED_COST,
    MultiObjectTracker,
    PrimarySelector,
    Track,
    hungarian_assign,
    point_in_polygon,
)


class FakeAppearance:
    ready = True

    def extract(self, source_frame: np.ndarray, bbox: dict[str, float]) -> np.ndarray:
        feature = np.asarray(
            [bbox["x"], bbox["y"], bbox["width"], bbox["height"], 1.0],
            dtype=np.float32,
        )
        return feature / np.linalg.norm(feature)

    def status(self) -> dict:
        return {"ready": True, "backend": "fake", "embedding_count": 0}


def detection(
    x: float,
    score: float = 0.9,
    width: float = 0.20,
    height: float = 0.50,
) -> dict:
    return {
        "score": score,
        "bbox": {"x": x, "y": 0.30, "width": width, "height": height},
        "area_ratio": width * height,
        "bottom_center": {"x": x + width / 2, "y": 0.30 + height},
    }


def tracker() -> MultiObjectTracker:
    settings = TrackingSettings(
        confirm_hits=2,
        confirm_window_frames=4,
        lost_timeout_seconds=2.0,
        new_track_threshold=0.6,
        high_threshold=0.5,
        low_threshold=0.1,
        appearance_similarity_gate=-1.0,
        mahalanobis_gate=1000.0,
    )
    return MultiObjectTracker(
        settings,
        PresenceSettings(engagement_min_area_ratio=0.08, engage_confirm_frames=2),
        PrimarySettings(acquire_stable_seconds=0.2),
        FakeAppearance(),
    )


def confirmed_track(
    track_id: int,
    item: dict,
    *,
    now: float,
    hits: int,
) -> Track:
    track = Track.create(track_id, item, now, gallery_size=10)
    track.state = "confirmed"
    track.hits = hits
    return track


def test_hungarian_rectangular_assignment() -> None:
    costs = np.asarray(
        [[0.1, 0.9, 0.8], [0.7, 0.2, 0.6]],
        dtype=np.float64,
    )
    assert sorted(hungarian_assign(costs, max_cost=0.5)) == [(0, 0), (1, 1)]


def test_hungarian_returns_when_every_pair_is_blocked() -> None:
    costs = np.full((2, 3), BLOCKED_COST, dtype=np.float64)
    assert hungarian_assign(costs, max_cost=0.8) == []


def test_point_in_polygon() -> None:
    polygon = [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]
    assert point_in_polygon((0.5, 0.5), polygon)
    assert not point_in_polygon((0.95, 0.5), polygon)


def test_track_confirms_and_recovers_after_short_occlusion() -> None:
    runtime = tracker()
    frame = np.zeros((200, 300, 3), dtype=np.uint8)

    tracks, events = runtime.update([detection(0.20)], frame, now=1.0)
    assert tracks[0]["state"] == "tentative"
    assert not events

    tracks, events = runtime.update([detection(0.205)], frame, now=1.1)
    assert tracks[0]["track_id"] == 1
    assert tracks[0]["state"] == "confirmed"
    assert any(event_type == "visitor_entered" for event_type, _ in events)

    tracks, _ = runtime.update([], frame, now=1.2)
    assert tracks[0]["state"] == "lost"

    tracks, events = runtime.update([detection(0.22)], frame, now=1.8)
    assert tracks[0]["track_id"] == 1
    assert tracks[0]["state"] == "confirmed"
    recovered = [
        payload
        for event_type, payload in events
        if event_type == "track_recovered"
    ]
    assert recovered and recovered[0]["lost_seconds"] >= 0.5
    assert tracks[0]["recovered_count"] == 1


def test_track_removed_after_occlusion_timeout() -> None:
    runtime = tracker()
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    runtime.update([detection(0.20)], frame, now=1.0)
    runtime.update([detection(0.20)], frame, now=1.1)
    runtime.update([], frame, now=1.2)
    _, events = runtime.update([], frame, now=3.3)
    assert any(event_type == "visitor_left" for event_type, _ in events)


def test_primary_selector_uses_hysteresis() -> None:
    presence = PresenceSettings()
    selector = PrimarySelector(
        PrimarySettings(
            acquire_stable_seconds=0.2,
            challenger_margin=0.2,
            challenger_hold_seconds=0.3,
        ),
        presence,
    )
    first = confirmed_track(1, detection(0.35), now=1.0, hits=20)
    selector.update([first], 1.0)
    primary_id, event = selector.update([first], 1.25)
    assert primary_id == 1
    assert event and event["current_track_id"] == 1

    second = confirmed_track(
        2,
        detection(0.30, width=0.45, height=0.65),
        now=1.3,
        hits=30,
    )
    primary_id, _ = selector.update([first, second], 1.3)
    assert primary_id == 1


def test_primary_lock_survives_short_occlusion_then_expires() -> None:
    presence = PresenceSettings()
    selector = OcclusionAwarePrimarySelector(
        PrimarySettings(acquire_stable_seconds=0.2, lost_lock_seconds=2.0),
        presence,
    )
    first = confirmed_track(1, detection(0.35), now=1.0, hits=20)
    selector.update([first], 1.0)
    primary_id, _ = selector.update([first], 1.25)
    assert primary_id == 1

    primary_id, event = selector.update([], 2.0)
    assert primary_id == 1
    assert event is None

    primary_id, event = selector.update([], 3.5)
    assert primary_id is None
    assert event and event["previous_track_id"] == 1
