from __future__ import annotations

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
