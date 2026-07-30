from __future__ import annotations

from app.client_protocol import build_client_state, visitor_from_track


def _track(*, primary: bool = True, engaged: bool = True, with_face: bool = True) -> dict:
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
    return {
        "track_id": 7,
        "visitor_session_id": "visitor_abc123",
        "state": "confirmed",
        "visible": True,
        "primary": primary,
        "engaged": engaged,
        "score": 0.93,
        "area_ratio": 0.21,
        "bbox": {"x": 0.35, "y": 0.10, "width": 0.30, "height": 0.82},
        "face": face,
        "identity": {
            "identity_id": "person_rico",
            "display_name": "Rico",
            "similarity": 0.78,
            "source": "sface_gallery",
        },
    }


def test_primary_engaged_face_is_greeting_eligible() -> None:
    visitor = visitor_from_track(_track())
    assert visitor["greeting_eligible"] is True
    assert visitor["visitor_session_id"] == "visitor_abc123"
    assert visitor["face"]["detected"] is True
    assert visitor["identity"]["display_name"] == "Rico"


def test_greeting_requires_primary_engaged_visible_face() -> None:
    assert visitor_from_track(_track(primary=False))["greeting_eligible"] is False
    assert visitor_from_track(_track(engaged=False))["greeting_eligible"] is False
    assert visitor_from_track(_track(with_face=False))["greeting_eligible"] is False


def test_client_state_contains_compact_primary_and_visitors() -> None:
    state = build_client_state(
        service="rtx-vision-edge-server",
        version="0.6.0",
        phase="phase4_identity_session_fusion",
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
    assert state["schema_version"] == "phase5.1"
    assert state["ready"] is True
    assert state["primary"]["track_id"] == 7
    assert state["primary"]["greeting_eligible"] is True
    assert len(state["visitors"]) == 1
