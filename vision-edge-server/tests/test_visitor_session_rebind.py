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


def test_provisional_new_session_rebinds_when_face_arrives() -> None:
    runtime = VisitorSessionRuntime(
        VisitorSessionSettings(
            face_high_similarity=0.80,
            face_medium_similarity=0.70,
            face_minimum_margin=0.01,
            body_high_similarity=0.95,
        )
    )
    face = normalize_embedding(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    old_body = normalize_embedding(np.asarray([0.0, 1.0, 0.0], dtype=np.float32))
    unrelated_body = normalize_embedding(np.asarray([0.0, 0.0, 1.0], dtype=np.float32))

    first, _ = runtime.update(
        [_track(1)],
        face_embeddings={1: face},
        body_embeddings={1: old_body},
        identities={1: None},
        now=1.0,
    )
    original_session = first[0]["visitor_session_id"]

    runtime.update(
        [_track(1, visible=False)],
        face_embeddings={1: None},
        body_embeddings={1: old_body},
        identities={1: None},
        now=2.0,
    )

    provisional, _ = runtime.update(
        [_track(8, x=0.6)],
        face_embeddings={8: None},
        body_embeddings={8: unrelated_body},
        identities={8: None},
        now=3.0,
    )
    provisional_session = provisional[0]["visitor_session_id"]
    assert provisional_session != original_session

    rebound, events = runtime.update(
        [_track(8, x=0.6)],
        face_embeddings={8: face},
        body_embeddings={8: unrelated_body},
        identities={8: None},
        now=3.5,
    )
    assert rebound[0]["visitor_session_id"] == original_session
    recovery = [event for event in events if event[0] == "visitor_session_recovered"]
    assert recovery
    assert recovery[0][1]["replaced_provisional_session_id"] == provisional_session
    assert runtime.snapshot(now=3.5)["session_count"] == 1


def test_medium_face_match_requires_repeated_evidence() -> None:
    runtime = VisitorSessionRuntime(
        VisitorSessionSettings(
            face_high_similarity=0.90,
            face_medium_similarity=0.70,
            face_minimum_margin=0.01,
            body_medium_similarity=0.70,
            medium_confirmations=2,
        )
    )
    reference_face = normalize_embedding(
        np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    )
    query_face = normalize_embedding(
        np.asarray([0.75, math.sqrt(1.0 - 0.75**2), 0.0], dtype=np.float32)
    )
    body = normalize_embedding(np.asarray([0.0, 1.0, 0.0], dtype=np.float32))

    first, _ = runtime.update(
        [_track(1)],
        face_embeddings={1: reference_face},
        body_embeddings={1: body},
        identities={1: None},
        now=1.0,
    )
    original_session = first[0]["visitor_session_id"]
    runtime.update(
        [_track(1, visible=False)],
        face_embeddings={1: None},
        body_embeddings={1: body},
        identities={1: None},
        now=2.0,
    )

    provisional, first_events = runtime.update(
        [_track(5, x=0.4)],
        face_embeddings={5: query_face},
        body_embeddings={5: body},
        identities={5: None},
        now=3.0,
    )
    provisional_id = provisional[0]["visitor_session_id"]
    assert provisional_id != original_session
    assert not any(
        event_type == "visitor_session_recovered"
        for event_type, _ in first_events
    )

    recovered, second_events = runtime.update(
        [_track(5, x=0.4)],
        face_embeddings={5: query_face},
        body_embeddings={5: body},
        identities={5: None},
        now=3.2,
    )
    assert recovered[0]["visitor_session_id"] == original_session
    recovery = [
        payload
        for event_type, payload in second_events
        if event_type == "visitor_session_recovered"
    ]
    assert recovery
    assert recovery[0]["reason"] == "face_body_medium"
    assert recovery[0]["replaced_provisional_session_id"] == provisional_id
