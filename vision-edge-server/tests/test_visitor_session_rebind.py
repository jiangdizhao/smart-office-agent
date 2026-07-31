from __future__ import annotations

import math

import numpy as np

from app.config import VisitorSessionSettings
from app.identity_runtime import normalize_embedding
from app.visitor_session import VisitorSessionRuntime


def _track(track_id: int, *, visible: bool = True, x: float = 0.2) -> dict:
    return {
        "track_id": track_id,
        "state": "confirmed" if visible else "lost",
        "visible": visible,
        "primary": True,
        "engaged": True,
        "score": 0.9,
        "area_ratio": 0.2,
        "bbox": {"x": x, "y": 0.2, "width": 0.3, "height": 0.7},
        "trail": [],
        "identity": None,
    }


def test_new_anonymous_track_immediately_gets_new_visit_even_with_same_face() -> None:
    runtime = VisitorSessionRuntime(VisitorSessionSettings())
    face = normalize_embedding(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    body = normalize_embedding(np.asarray([0.0, 1.0, 0.0], dtype=np.float32))

    first, _ = runtime.update(
        [_track(1)],
        face_embeddings={1: face},
        body_embeddings={1: body},
        identities={1: None},
        now=1.0,
    )
    original_visit = first[0]["visitor_session_id"]
    runtime.update(
        [_track(1, visible=False)],
        face_embeddings={1: None},
        body_embeddings={1: body},
        identities={1: None},
        now=1.5,
    )

    created, events = runtime.update(
        [_track(8, x=0.6)],
        face_embeddings={8: face},
        body_embeddings={8: body},
        identities={8: None},
        now=1.6,
    )
    assert created[0]["visitor_session_id"] != original_visit
    assert created[0]["visitor_session_pending"] is False
    assert any(event_type == "visitor_session_started" for event_type, _ in events)
    assert not any(event_type == "visitor_session_recovered" for event_type, _ in events)


def test_repeated_anonymous_face_evidence_never_restores_old_visit() -> None:
    runtime = VisitorSessionRuntime(VisitorSessionSettings())
    reference_face = normalize_embedding(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    query_face = normalize_embedding(
        np.asarray([0.75, math.sqrt(1.0 - 0.75**2), 0.0], dtype=np.float32)
    )
    body = normalize_embedding(np.asarray([0.0, 1.0, 0.0], dtype=np.float32))

    first, _ = runtime.update(
        [_track(1)],
        face_embeddings={1: reference_face},
        body_embeddings={1: body},
        identities={1: None},
        now=10.0,
    )
    original_visit = first[0]["visitor_session_id"]
    runtime.update(
        [_track(1, visible=False)],
        face_embeddings={1: None},
        body_embeddings={1: body},
        identities={1: None},
        now=10.5,
    )

    created, first_events = runtime.update(
        [_track(5, x=0.4)],
        face_embeddings={5: query_face},
        body_embeddings={5: body},
        identities={5: None},
        now=10.6,
    )
    new_visit = created[0]["visitor_session_id"]
    assert new_visit != original_visit
    assert any(event_type == "visitor_session_started" for event_type, _ in first_events)
    assert not any(event_type == "visitor_session_recovered" for event_type, _ in first_events)

    stable, later_events = runtime.update(
        [_track(5, x=0.4)],
        face_embeddings={5: query_face},
        body_embeddings={5: body},
        identities={5: None},
        now=10.8,
    )
    assert stable[0]["visitor_session_id"] == new_visit
    assert not any(event_type == "visitor_session_started" for event_type, _ in later_events)


def test_registered_visit_recovers_only_after_independent_identity_confirmation() -> None:
    runtime = VisitorSessionRuntime(VisitorSessionSettings())
    face = normalize_embedding(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    body = normalize_embedding(np.asarray([0.0, 1.0, 0.0], dtype=np.float32))
    rico = {
        "identity_id": "person_rico",
        "display_name": "Rico",
        "similarity": 0.9,
        "source": "sface_gallery",
    }

    first, _ = runtime.update(
        [_track(11)],
        face_embeddings={11: face},
        body_embeddings={11: body},
        identities={11: rico},
        now=20.0,
    )
    original_visit = first[0]["visitor_session_id"]
    runtime.update(
        [_track(11, visible=False)],
        face_embeddings={11: None},
        body_embeddings={11: body},
        identities={11: None},
        now=20.5,
    )

    stranger, stranger_events = runtime.update(
        [_track(12, x=0.6)],
        face_embeddings={12: face},
        body_embeddings={12: body},
        identities={12: None},
        now=20.6,
    )
    assert stranger[0]["visitor_session_id"] != original_visit
    assert stranger[0].get("identity") is None
    assert not any(event_type == "visitor_session_recovered" for event_type, _ in stranger_events)

    # Use a fresh runtime to test a genuine tracker-ID change for Rico. The new
    # track must independently carry the same confirmed identity.
    runtime = VisitorSessionRuntime(VisitorSessionSettings())
    first, _ = runtime.update(
        [_track(21)],
        face_embeddings={21: face},
        body_embeddings={21: body},
        identities={21: rico},
        now=30.0,
    )
    original_visit = first[0]["visitor_session_id"]
    runtime.update(
        [_track(21, visible=False)],
        face_embeddings={21: None},
        body_embeddings={21: body},
        identities={21: None},
        now=30.5,
    )
    recovered, events = runtime.update(
        [_track(22)],
        face_embeddings={22: face},
        body_embeddings={22: body},
        identities={22: rico},
        now=30.6,
    )
    assert recovered[0]["visitor_session_id"] == original_visit
    assert any(
        event_type == "visitor_session_recovered"
        and payload["reason"] == "independently_confirmed_registered_identity"
        for event_type, payload in events
    )
