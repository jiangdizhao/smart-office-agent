from __future__ import annotations

from typing import Any

CLIENT_SCHEMA_VERSION = "phase6.0"
ANONYMOUS_SESSION_STABILIZATION_SECONDS = 0.75


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _public_identity(identity: Any) -> dict[str, Any] | None:
    if not isinstance(identity, dict):
        return None
    identity_id = identity.get("identity_id")
    display_name = identity.get("display_name")
    if not identity_id and not display_name:
        return None
    return {
        "identity_id": identity_id,
        "display_name": display_name,
        "similarity": _number(identity.get("similarity"), 0.0),
        "source": identity.get("source"),
    }


def _session_profile(track: dict[str, Any], identity: dict[str, Any] | None) -> dict[str, Any]:
    raw_session_id = track.get("visitor_session_id")
    session = track.get("visitor_session") if isinstance(track.get("visitor_session"), dict) else {}
    age_seconds = max(0.0, _number(session.get("age_seconds"), 0.0))
    last_seen_age_seconds = max(0.0, _number(session.get("last_seen_age_seconds"), 0.0))
    recovery_count = max(0, _integer(session.get("recovery_count"), 0))
    assignment_stable = bool(session.get("assignment_stable"))
    registered = identity is not None
    returning = bool(registered or recovery_count > 0)
    stable = bool(
        raw_session_id
        and (
            assignment_stable
            or registered
            or recovery_count > 0
            or age_seconds >= ANONYMOUS_SESSION_STABILIZATION_SECONDS
        )
    )
    if registered:
        greeting_kind = "registered_identity"
    elif recovery_count > 0:
        greeting_kind = "returning_anonymous"
    else:
        greeting_kind = "new_anonymous"
    return {
        "raw_session_id": raw_session_id,
        "visitor_session_id": raw_session_id if stable else None,
        "visit_id": raw_session_id if stable else None,
        "provisional_session_id": raw_session_id if raw_session_id and not stable else None,
        "session_stable": stable,
        "session_assignment_stable": assignment_stable,
        "session_age_seconds": age_seconds,
        "session_last_seen_age_seconds": last_seen_age_seconds,
        "session_recovery_count": recovery_count,
        "returning_visitor": returning,
        "greeting_kind": greeting_kind,
    }


def visitor_from_track(track: dict[str, Any]) -> dict[str, Any]:
    bbox = track.get("bbox") if isinstance(track.get("bbox"), dict) else {}
    face = track.get("face") if isinstance(track.get("face"), dict) else None
    quality = face.get("quality") if face and isinstance(face.get("quality"), dict) else {}
    face_bbox = face.get("bbox") if face and isinstance(face.get("bbox"), dict) else {}
    face_area_ratio = _number(face_bbox.get("width")) * _number(face_bbox.get("height"))
    center_x = _number(bbox.get("x")) + _number(bbox.get("width")) / 2.0
    center_y = _number(bbox.get("y")) + _number(bbox.get("height")) / 2.0
    visible = bool(track.get("visible"))
    confirmed = track.get("state") == "confirmed"
    primary = bool(track.get("primary"))
    engaged = bool(track.get("engaged"))
    identity = _public_identity(track.get("identity"))
    session_profile = _session_profile(track, identity)
    visitor_session_id = session_profile["visitor_session_id"]
    face_detected = face is not None
    greeting_eligible = bool(
        visible
        and confirmed
        and primary
        and engaged
        and visitor_session_id
        and session_profile["session_stable"]
    )
    return {
        "track_id": int(track.get("track_id") or 0),
        "visitor_session_id": visitor_session_id,
        "visit_id": session_profile["visit_id"],
        "visit_state": "visible" if visible else "retained",
        "provisional_session_id": session_profile["provisional_session_id"],
        "session_stable": session_profile["session_stable"],
        "session_assignment_stable": session_profile["session_assignment_stable"],
        "session_age_seconds": session_profile["session_age_seconds"],
        "session_last_seen_age_seconds": session_profile["session_last_seen_age_seconds"],
        "session_recovery_count": session_profile["session_recovery_count"],
        "returning_visitor": session_profile["returning_visitor"],
        "greeting_kind": session_profile["greeting_kind"],
        "state": track.get("state"),
        "visible": visible,
        "primary": primary,
        "engaged": engaged,
        "score": _number(track.get("score")),
        "body_area_ratio": _number(track.get("area_ratio")),
        "center_x": max(0.0, min(1.0, center_x)),
        "center_y": max(0.0, min(1.0, center_y)),
        "face": {
            "detected": face_detected,
            "area_ratio": face_area_ratio,
            "confidence": _number(quality.get("face_confidence")),
            "frontal_score": _number(quality.get("frontal_score")),
            "quality_score": _number(quality.get("score")),
            "recognition_usable": bool(face and face.get("recognition_usable")),
            "enrollment_usable": bool(face and face.get("enrollment_usable")),
            "stable_frames": int(
                (face or {}).get("recognition_stable_frames")
                or (face or {}).get("stable_frames")
                or 0
            ),
        },
        "identity": identity,
        "greeting_eligible": greeting_eligible,
    }


def build_client_state(
    *,
    service: str,
    version: str,
    phase: str,
    ready: bool,
    degraded_reasons: list[str],
    tracks_snapshot: dict[str, Any],
    uptime_seconds: float,
) -> dict[str, Any]:
    raw_tracks = tracks_snapshot.get("tracks") or []
    visitors = [
        visitor_from_track(track)
        for track in raw_tracks
        if isinstance(track, dict) and track.get("state") != "removed"
    ]
    retained_primary = next((visitor for visitor in visitors if visitor["primary"]), None)
    visible_primary = next(
        (visitor for visitor in visitors if visitor["primary"] and visitor["visible"]),
        None,
    )
    return {
        "schema_version": CLIENT_SCHEMA_VERSION,
        "service": service,
        "version": version,
        "phase": phase,
        "ready": bool(ready),
        "degraded_reasons": list(degraded_reasons),
        "uptime_seconds": float(uptime_seconds),
        "scene_state": tracks_snapshot.get("scene_state", "unknown"),
        "person_count": int(tracks_snapshot.get("person_count") or 0),
        "primary_track_id": (
            None if visible_primary is None else visible_primary["track_id"]
        ),
        "primary": visible_primary,
        "retained_primary": retained_primary,
        "visitors": visitors,
    }
