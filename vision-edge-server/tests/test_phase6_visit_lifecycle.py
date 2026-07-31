from __future__ import annotations

from app.client_protocol import build_client_state
from app.config import VisitorSessionSettings, load_config
from app.visitor_session import VisitorSessionRuntime


def _track(
    track_id: int,
    *,
    visible: bool = True,
    primary: bool = True,
    identity: dict | None = None,
) -> dict:
    return {
        "track_id": track_id,
        "state": "confirmed" if visible else "lost",
        "visible": visible,
        "primary": primary,
        "engaged": visible,
        "score": 0.94,
        "area_ratio": 0.21,
        "bbox": {"x": 0.35, "y": 0.12, "width": 0.30, "height": 0.70},
        "identity": identity,
    }


def _update(
    runtime: VisitorSessionRuntime,
    tracks: list[dict],
    identities: dict[int, dict | None],
    now: float,
):
    track_ids = {int(track["track_id"]) for track in tracks}
    return runtime.update(
        tracks,
        face_embeddings={track_id: None for track_id in track_ids},
        body_embeddings={track_id: None for track_id in track_ids},
        identities=identities,
        now=now,
    )


def test_config_loads_the_two_second_visit_setting() -> None:
    config, _ = load_config()
    assert config.version == "0.7.0"
    assert config.phase == "phase6_visit_lifecycle_identity_isolation"
    assert config.visitor_session.ttl_seconds == 2.0
    assert config.identity.adaptive_confirmation_enabled is False
    assert config.identity.cosine_threshold == 0.62
    assert config.identity.confirm_observations == 3


def test_client_primary_means_a_visible_red_box() -> None:
    retained = _track(7, visible=False)
    retained["visitor_session_id"] = "visitor_retained"
    retained["visitor_session"] = {
        "age_seconds": 3.0,
        "last_seen_age_seconds": 1.0,
        "recovery_count": 0,
        "assignment_stable": True,
    }
    state = build_client_state(
        service="rtx-vision-edge-server",
        version="0.7.0",
        phase="phase6_visit_lifecycle_identity_isolation",
        ready=True,
        degraded_reasons=[],
        tracks_snapshot={
            "tracks": [retained],
            "scene_state": "primary_lost",
            "person_count": 0,
        },
        uptime_seconds=10.0,
    )
    assert state["schema_version"] == "phase6.0"
    assert state["primary"] is None
    assert state["primary_track_id"] is None
    assert state["retained_primary"]["track_id"] == 7
    assert state["retained_primary"]["visible"] is False

    visible = _track(8)
    visible["visitor_session_id"] = "visitor_visible"
    visible["visitor_session"] = {
        "age_seconds": 1.0,
        "last_seen_age_seconds": 0.0,
        "recovery_count": 0,
        "assignment_stable": True,
    }
    visible_state = build_client_state(
        service="rtx-vision-edge-server",
        version="0.7.0",
        phase="phase6_visit_lifecycle_identity_isolation",
        ready=True,
        degraded_reasons=[],
        tracks_snapshot={
            "tracks": [visible],
            "scene_state": "engaged",
            "person_count": 1,
        },
        uptime_seconds=11.0,
    )
    assert visible_state["primary"]["track_id"] == 8
    assert visible_state["primary"]["visit_id"] == "visitor_visible"
    assert visible_state["primary"]["greeting_eligible"] is True


def test_visit_expires_after_two_seconds_and_new_track_gets_new_id() -> None:
    runtime = VisitorSessionRuntime(VisitorSessionSettings(ttl_seconds=5.0))
    first_tracks, _ = _update(runtime, [_track(1)], {1: None}, now=10.0)
    first_visit = first_tracks[0]["visitor_session_id"]

    _, early_events = _update(runtime, [], {}, now=11.9)
    assert not any(event_type == "visitor_session_expired" for event_type, _ in early_events)

    _, end_events = _update(runtime, [], {}, now=12.01)
    assert any(
        event_type == "visitor_session_expired"
        and payload["visitor_session_id"] == first_visit
        for event_type, payload in end_events
    )
    assert runtime.snapshot(now=12.01)["visit_absence_seconds"] == 2.0
    assert runtime.snapshot(now=12.01)["session_count"] == 0

    next_tracks, _ = _update(runtime, [_track(2)], {2: None}, now=12.1)
    assert next_tracks[0]["visitor_session_id"] != first_visit


def test_anonymous_track_cannot_inherit_registered_visit_identity() -> None:
    runtime = VisitorSessionRuntime(VisitorSessionSettings(ttl_seconds=5.0))
    rico = {
        "identity_id": "person_rico",
        "display_name": "Rico",
        "similarity": 0.91,
        "source": "sface_gallery",
    }
    registered_tracks, _ = _update(runtime, [_track(10, identity=rico)], {10: rico}, now=20.0)
    registered_visit = registered_tracks[0]["visitor_session_id"]
    _update(runtime, [], {}, now=20.5)

    stranger_tracks, _ = _update(runtime, [_track(11, identity=None)], {11: None}, now=20.6)
    stranger = stranger_tracks[0]
    assert stranger["visitor_session_id"] != registered_visit
    assert stranger.get("identity") is None
    assert stranger.get("identity_source") is None


def test_registered_recovery_requires_independent_same_identity_confirmation() -> None:
    runtime = VisitorSessionRuntime(VisitorSessionSettings(ttl_seconds=5.0))
    rico = {
        "identity_id": "person_rico",
        "display_name": "Rico",
        "similarity": 0.91,
        "source": "sface_gallery",
    }
    first_tracks, _ = _update(runtime, [_track(21, identity=rico)], {21: rico}, now=30.0)
    first_visit = first_tracks[0]["visitor_session_id"]
    _update(runtime, [], {}, now=30.5)

    recovered_tracks, events = _update(
        runtime,
        [_track(22, identity=rico)],
        {22: rico},
        now=30.6,
    )
    assert recovered_tracks[0]["visitor_session_id"] == first_visit
    assert any(
        event_type == "visitor_session_recovered"
        and payload["reason"] == "independently_confirmed_registered_identity"
        for event_type, payload in events
    )
