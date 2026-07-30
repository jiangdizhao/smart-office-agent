from __future__ import annotations

from pathlib import Path

import numpy as np

from app.config import FaceSettings, IdentitySettings
from app.face_runtime import analyse_face_quality
from app.identity_runtime import IdentityRuntime, IdentityStore, normalize_embedding


def synthetic_face_row() -> np.ndarray:
    return np.asarray(
        [
            60,
            40,
            120,
            140,
            95,
            85,
            145,
            85,
            120,
            112,
            100,
            145,
            140,
            145,
            0.95,
        ],
        dtype=np.float32,
    )


def test_face_quality_accepts_large_sharp_frontal_face() -> None:
    rng = np.random.default_rng(7)
    frame = rng.integers(70, 190, size=(240, 240, 3), dtype=np.uint8)
    settings = FaceSettings(
        min_face_width_pixels=80,
        min_face_height_pixels=80,
        min_sharpness=5,
        min_frontal_score=0.4,
        min_quality_score=0.4,
        min_brightness=20,
        max_brightness=235,
    )
    quality = analyse_face_quality(frame, synthetic_face_row(), settings)
    assert quality["ready"] is True
    assert quality["frontal_score"] > 0.4
    assert quality["sharpness"] > 5


def test_identity_store_crud_and_prototype(tmp_path: Path) -> None:
    store = IdentityStore(
        tmp_path / "identities.sqlite3", max_samples_per_identity=3
    )
    first = store.enroll(
        display_name="Rico",
        embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        quality_score=0.9,
        consent_at_unix=10.0,
        metadata={"test": True},
    )
    identity_id = first["identity_id"]
    store.enroll(
        identity_id=identity_id,
        display_name="Rico",
        embedding=np.asarray([0.95, 0.05, 0.0], dtype=np.float32),
        quality_score=0.8,
        consent_at_unix=10.0,
    )
    listed = store.list_identities()
    assert len(listed) == 1
    assert listed[0]["display_name"] == "Rico"
    assert listed[0]["sample_count"] == 2
    prototypes = store.prototypes()
    assert identity_id in prototypes
    _, prototype = prototypes[identity_id]
    assert np.isclose(np.linalg.norm(prototype), 1.0)
    assert store.delete(identity_id) is True
    assert store.list_identities() == []


def test_identity_confirmation_requires_repeated_match_and_margin(
    tmp_path: Path,
) -> None:
    settings = IdentitySettings(
        enabled=False,
        database_path=str(tmp_path / "identities.sqlite3"),
        cosine_threshold=0.70,
        minimum_margin=0.10,
        confirm_observations=3,
    )
    runtime = IdentityRuntime(settings, tmp_path)
    runtime._prototypes = {
        "person_a": (
            {
                "identity_id": "person_a",
                "display_name": "Alice",
                "external_id": None,
                "consent_at_unix": 1.0,
            },
            normalize_embedding(
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
            ),
        ),
        "person_b": (
            {
                "identity_id": "person_b",
                "display_name": "Bob",
                "external_id": None,
                "consent_at_unix": 1.0,
            },
            normalize_embedding(
                np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
            ),
        ),
    }
    query = normalize_embedding(
        np.asarray([0.98, 0.02, 0.0], dtype=np.float32)
    )
    result, event = runtime.match_embedding(
        track_id=7,
        embedding=query,
        quality_score=0.9,
        now_monotonic=1.0,
    )
    assert result is None and event is None
    result, event = runtime.match_embedding(
        track_id=7,
        embedding=query,
        quality_score=0.9,
        now_monotonic=1.2,
    )
    assert result is None and event is None
    result, event = runtime.match_embedding(
        track_id=7,
        embedding=query,
        quality_score=0.9,
        now_monotonic=1.4,
    )
    assert result and result["identity_id"] == "person_a"
    assert event and event[0] == "visitor_identified"
