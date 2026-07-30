from __future__ import annotations

from typing import Any

CLIENT_SCHEMA_VERSION = "phase5.1"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
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
    visitor_session_id = track.get("visitor_session_id")
    face_detected = face is not None
    greeting_eligible = bool(
        visible
        and confirmed
        and primary
        and engaged
        and visitor_session_id
        and face_detected
    )
    return {
        "track_id": int(track.get("track_id") or 0),
        "visitor_session_id": visitor_session_id,
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
        "identity": _public_identity(track.get("identity")),
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
    primary = next((visitor for visitor in visitors if visitor["primary"]), None)
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
        "primary_track_id": tracks_snapshot.get("primary_track_id"),
        "primary": primary,
        "visitors": visitors,
    }
