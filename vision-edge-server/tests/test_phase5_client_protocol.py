from __future__ import annotations

from app.client_protocol import build_client_state, visitor_from_track


def _track(
    *,
    primary: bool = True,
    engaged: bool = True,
    with_face: bool = True,
    identity: bool = True,
    session_age_seconds: float = 12.0,
    recovery_count: int = 0,
) -> dict:
    face = None
    if with_face:
        face = {
            "bbox": {"x": 0.45, "y": 0.18, "width": 0.10, "height": 0.16},
            "recognition_usable": True,
            "enrollment_usable": False,
            "recognition_stable_frames": 2,
            "quality": {
                "face_confidence": 0.91,
                "frontal_score": 0.82,
                "score": 0.76,
            },
        }
    identity_payload = None
    if identity:
        identity_payload = {
            "identity_id": "person_rico",
            "display_name": "Rico",
            "similarity": 0.78,
            "source": "sface_gallery",
        }
    return {
        "track_id": 7,
        "visitor_session_id": "visitor_abc123",
        "visitor_session": {
            "age_seconds": session_age_seconds,
            "recovery_count": recovery_count,
        },
        "state": "confirmed",
        "visible": True,
        "primary": primary,
        "engaged": engaged,
        "score": 0.93,
        "area_ratio": 0.21,
        "bbox": {"x": 0.35, "y": 0.10, "width": 0.30, "height": 0.82},
        "face": face,
        "identity": identity_payload,
    }


def test_registered_primary_is_immediately_greeting_eligible() -> None:
    visitor = visitor_from_track(_track())
    assert visitor["greeting_eligible"] is True
    assert visitor["visitor_session_id"] == "visitor_abc123"
    assert visitor["session_stable"] is True
    assert visitor["returning_visitor"] is True
    assert visitor["greeting_kind"] == "registered_identity"
    assert visitor["face"]["detected"] is True
    assert visitor["identity"]["display_name"] == "Rico"


def test_greeting_requires_visible_primary_engaged_visit_not_face() -> None:
    assert visitor_from_track(_track(primary=False))["greeting_eligible"] is False
    assert visitor_from_track(_track(engaged=False))["greeting_eligible"] is False
    assert visitor_from_track(_track(with_face=False))["greeting_eligible"] is True


def test_new_anonymous_session_is_hidden_only_during_short_stabilization() -> None:
    visitor = visitor_from_track(
        _track(identity=False, session_age_seconds=0.5, recovery_count=0)
    )
    assert visitor["visitor_session_id"] is None
    assert visitor["provisional_session_id"] == "visitor_abc123"
    assert visitor["session_stable"] is False
    assert visitor["greeting_eligible"] is False
    assert visitor["greeting_kind"] == "new_anonymous"


def test_new_anonymous_session_becomes_eligible_after_stabilization() -> None:
    visitor = visitor_from_track(
        _track(identity=False, session_age_seconds=1.0, recovery_count=0)
    )
    assert visitor["visitor_session_id"] == "visitor_abc123"
    assert visitor["session_stable"] is True
    assert visitor["returning_visitor"] is False
    assert visitor["greeting_eligible"] is True
    assert visitor["greeting_kind"] == "new_anonymous"


def test_recovered_anonymous_session_is_welcome_back_candidate() -> None:
    visitor = visitor_from_track(
        _track(identity=False, session_age_seconds=1.0, recovery_count=1)
    )
    assert visitor["visitor_session_id"] == "visitor_abc123"
    assert visitor["session_stable"] is True
    assert visitor["returning_visitor"] is True
    assert visitor["greeting_eligible"] is True
    assert visitor["greeting_kind"] == "returning_anonymous"


def test_client_state_contains_compact_primary_and_visitors() -> None:
    state = build_client_state(
        service="rtx-vision-edge-server",
        version="0.7.0",
        phase="phase6_visit_lifecycle_identity_isolation",
        ready=True,
        degraded_reasons=[],
        tracks_snapshot={
            "scene_state": "engaged",
            "person_count": 1,
            "primary_track_id": 7,
            "tracks": [_track()],
        },
        uptime_seconds=12.5,
    )
    assert state["schema_version"] == "phase6.0"
    assert state["ready"] is True
    assert state["primary"]["track_id"] == 7
    assert state["primary"]["greeting_eligible"] is True
    assert state["primary"]["greeting_kind"] == "registered_identity"
    assert len(state["visitors"]) == 1
