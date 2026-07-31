from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from app.config import FaceSettings, IdentitySettings, VisitorSessionSettings
from app.face_runtime import analyse_face_quality
from app.identity_runtime import IdentityRuntime, normalize_embedding
from app.visitor_session import VisitorSessionRuntime


def face_row() -> np.ndarray:
    return np.asarray(
        [
            40,
            30,
            120,
            140,
            72,
            72,
            128,
            72,
            100,
            100,
            80,
            138,
            120,
            138,
            0.95,
        ],
        dtype=np.float32,
    )


def track(track_id: int, x: float = 0.25, *, visible: bool = True) -> dict:
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


def test_recognition_gate_is_looser_than_enrollment_gate() -> None:
    frame = np.full((240, 240, 3), 128, dtype=np.uint8)
    settings = FaceSettings(
        recognition_min_face_width_pixels=16,
        recognition_min_face_height_pixels=16,
        recognition_min_sharpness=0,
        recognition_min_brightness=0,
        recognition_max_brightness=255,
        recognition_min_frontal_score=0,
        recognition_min_quality_score=0,
        min_face_width_pixels=16,
        min_face_height_pixels=16,
        min_sharpness=1000,
        min_brightness=0,
        max_brightness=254,
        min_frontal_score=0,
        min_quality_score=0,
    )
    quality = analyse_face_quality(frame, face_row(), settings)
    assert quality["recognition_candidate"] is True
    assert quality["enrollment_candidate"] is False
    assert any(
        "sharpness" in reason
        for reason in quality["enrollment_rejection_reasons"]
    )


def test_adaptive_high_confidence_identity_is_immediate(tmp_path: Path) -> None:
    settings = IdentitySettings(
        enabled=False,
        database_path=str(tmp_path / "identity.sqlite3"),
        adaptive_confirmation_enabled=True,
        high_similarity_threshold=0.80,
        high_minimum_margin=0.01,
        high_confirm_observations=1,
        medium_similarity_threshold=0.60,
        low_similarity_threshold=0.40,
    )
    runtime = IdentityRuntime(settings, tmp_path)
    runtime._prototypes = {
        "rico": (
            {
                "identity_id": "rico",
                "display_name": "Rico",
                "external_id": None,
                "consent_at_unix": 1.0,
            },
            normalize_embedding(
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
            ),
        ),
        "alice": (
            {
                "identity_id": "alice",
                "display_name": "Alice",
                "external_id": None,
                "consent_at_unix": 1.0,
            },
            normalize_embedding(
                np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
            ),
        ),
    }
    result, event = runtime.match_embedding(
        track_id=10,
        embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        quality_score=0.7,
        now_monotonic=1.0,
    )
    assert result is not None
    assert result["display_name"] == "Rico"
    assert result["decision_tier"] == "high"
    assert event and event[0] == "visitor_identified"


def test_duplicate_display_names_are_pooled_not_competitors(
    tmp_path: Path,
) -> None:
    settings = IdentitySettings(
        enabled=False,
        database_path=str(tmp_path / "identity.sqlite3"),
        adaptive_confirmation_enabled=True,
        high_similarity_threshold=0.80,
        high_minimum_margin=0.01,
        high_confirm_observations=1,
    )
    runtime = IdentityRuntime(settings, tmp_path)
    runtime.store.enroll(
        identity_id="rico_a",
        display_name="Rico",
        embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        quality_score=0.9,
        consent_at_unix=1.0,
    )
    runtime.store.enroll(
        identity_id="rico_b",
        display_name="rico",
        embedding=np.asarray([0.99, 0.01, 0.0], dtype=np.float32),
        quality_score=0.8,
        consent_at_unix=1.0,
    )
    runtime.store.enroll(
        identity_id="alice",
        display_name="Alice",
        embedding=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        quality_score=0.9,
        consent_at_unix=1.0,
    )
    runtime.reload_gallery()
    snapshot = runtime.snapshot()
    assert snapshot["identity_count"] == 2
    assert snapshot["database_identity_row_count"] == 3
    assert snapshot["duplicate_name_groups"][0]["pooled_sample_count"] == 2
    result, _ = runtime.match_embedding(
        track_id=1,
        embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        quality_score=0.8,
        now_monotonic=1.0,
    )
    assert result and result["display_name"].casefold() == "rico"


def test_enrollment_reuses_name_and_saves_multiple_samples(
    tmp_path: Path,
) -> None:
    settings = IdentitySettings(
        enabled=False,
        database_path=str(tmp_path / "identity.sqlite3"),
        enrollment_capture_seconds=0,
        enrollment_prebuffer_seconds=5,
        enrollment_min_samples=3,
        enrollment_target_samples=3,
        enrollment_max_selected_samples=3,
        enrollment_min_quality=0.5,
    )
    runtime = IdentityRuntime(settings, tmp_path)
    base = time.monotonic()
    for index, vector in enumerate(
        (
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.98, 0.02, 0.0],
        )
    ):
        runtime.record_embedding_sample(
            track_id=4,
            embedding=np.asarray(vector, dtype=np.float32),
            quality_score=0.9 - index * 0.05,
            enrollment_candidate=True,
            now_monotonic=base + index * 0.01,
        )
    first = runtime.enroll(track_id=4, display_name="Rico", consent=True)
    assert first["samples_added"] == 3
    identity_id = first["identity_id"]

    runtime.record_embedding_sample(
        track_id=5,
        embedding=np.asarray([0.97, 0.03, 0.0], dtype=np.float32),
        quality_score=0.85,
        enrollment_candidate=True,
        now_monotonic=time.monotonic(),
    )
    runtime.settings = settings.model_copy(
        update={"enrollment_min_samples": 1, "enrollment_target_samples": 1}
    )
    second = runtime.enroll(track_id=5, display_name="rico", consent=True)
    assert second["identity_id"] == identity_id
    assert second["reused_existing_identity"] is True


def test_anonymous_face_memory_does_not_recover_expired_visit() -> None:
    runtime = VisitorSessionRuntime(
        VisitorSessionSettings(
            face_high_similarity=0.80,
            face_medium_similarity=0.70,
            face_minimum_margin=0.01,
        )
    )
    face = normalize_embedding(
        np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    )
    body = normalize_embedding(
        np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
    )
    first_tracks, _ = runtime.update(
        [track(1)],
        face_embeddings={1: face},
        body_embeddings={1: body},
        identities={1: None},
        now=1.0,
    )
    session_id = first_tracks[0]["visitor_session_id"]
    runtime.update(
        [track(1, visible=False)],
        face_embeddings={1: None},
        body_embeddings={1: body},
        identities={1: None},
        now=2.0,
    )
    new_tracks, events = runtime.update(
        [track(9, x=0.55)],
        face_embeddings={9: face},
        body_embeddings={9: body},
        identities={9: None},
        now=4.0,
    )
    assert new_tracks[0]["visitor_session_id"] != session_id
    assert any(event_type == "visitor_session_started" for event_type, _ in events)
    assert not any(event_type == "visitor_session_recovered" for event_type, _ in events)


def test_registered_identity_is_not_propagated_without_new_confirmation() -> None:
    runtime = VisitorSessionRuntime(VisitorSessionSettings())
    identity = {
        "identity_id": "rico",
        "display_name": "Rico",
        "similarity": 0.9,
        "consent_at_unix": 1.0,
    }
    face = normalize_embedding(
        np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    )
    first, _ = runtime.update(
        [track(1)],
        face_embeddings={1: face},
        body_embeddings={1: None},
        identities={1: identity},
        now=1.0,
    )
    session_id = first[0]["visitor_session_id"]
    runtime.update(
        [track(1, visible=False)],
        face_embeddings={1: None},
        body_embeddings={1: None},
        identities={1: None},
        now=2.0,
    )
    second, events = runtime.update(
        [track(2)],
        face_embeddings={2: face},
        body_embeddings={2: None},
        identities={2: None},
        now=3.0,
    )
    assert second[0]["visitor_session_id"] != session_id
    assert second[0].get("identity") is None
    assert second[0].get("identity_source") is None
    assert not any(event_type == "visitor_session_recovered" for event_type, _ in events)
