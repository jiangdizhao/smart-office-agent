from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from app.config import IdentitySettings


class IdentityRuntimeError(RuntimeError):
    pass


def normalize_embedding(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        raise ValueError("face embedding has zero norm")
    return vector / norm


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if left.size != right.size:
        return -1.0
    return float(np.clip(np.dot(left, right), -1.0, 1.0))


class IdentityStore:
    def __init__(self, database_path: Path, max_samples_per_identity: int) -> None:
        self.database_path = database_path
        self.max_samples_per_identity = max_samples_per_identity
        self._lock = threading.RLock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS identities (
                    identity_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    external_id TEXT,
                    consent_at_unix REAL NOT NULL,
                    created_at_unix REAL NOT NULL,
                    updated_at_unix REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS identity_samples (
                    sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity_id TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    dimensions INTEGER NOT NULL,
                    quality_score REAL NOT NULL,
                    created_at_unix REAL NOT NULL,
                    FOREIGN KEY(identity_id) REFERENCES identities(identity_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_identity_samples_identity
                    ON identity_samples(identity_id, created_at_unix DESC);
                """
            )

    def enroll(
        self,
        *,
        display_name: str,
        embedding: np.ndarray,
        quality_score: float,
        consent_at_unix: float,
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        identity_id: str | None = None,
    ) -> dict[str, Any]:
        clean_name = display_name.strip()
        if not clean_name:
            raise ValueError("display_name is required")
        vector = normalize_embedding(embedding)
        now = time.time()
        identity_id = identity_id or f"person_{uuid.uuid4().hex}"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT identity_id FROM identities WHERE identity_id = ?", (identity_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO identities(
                        identity_id, display_name, external_id, consent_at_unix,
                        created_at_unix, updated_at_unix, metadata_json, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        identity_id,
                        clean_name,
                        external_id,
                        consent_at_unix,
                        now,
                        now,
                        metadata_json,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE identities
                    SET display_name = ?, external_id = ?, consent_at_unix = ?,
                        updated_at_unix = ?, metadata_json = ?, active = 1
                    WHERE identity_id = ?
                    """,
                    (
                        clean_name,
                        external_id,
                        consent_at_unix,
                        now,
                        metadata_json,
                        identity_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO identity_samples(identity_id, embedding, dimensions, quality_score, created_at_unix)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    identity_id,
                    vector.astype(np.float32).tobytes(),
                    int(vector.size),
                    float(quality_score),
                    now,
                ),
            )
            rows = connection.execute(
                """
                SELECT sample_id FROM identity_samples
                WHERE identity_id = ? ORDER BY created_at_unix DESC, sample_id DESC
                """,
                (identity_id,),
            ).fetchall()
            for row in rows[self.max_samples_per_identity :]:
                connection.execute(
                    "DELETE FROM identity_samples WHERE sample_id = ?", (int(row["sample_id"]),)
                )
        return self.get(identity_id) or {}

    def list_identities(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT i.*, COUNT(s.sample_id) AS sample_count
                FROM identities i
                LEFT JOIN identity_samples s ON s.identity_id = i.identity_id
                WHERE i.active = 1
                GROUP BY i.identity_id
                ORDER BY i.display_name COLLATE NOCASE, i.created_at_unix
                """
            ).fetchall()
        return [self._row_to_public(row) for row in rows]

    def get(self, identity_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT i.*, COUNT(s.sample_id) AS sample_count
                FROM identities i
                LEFT JOIN identity_samples s ON s.identity_id = i.identity_id
                WHERE i.identity_id = ? AND i.active = 1
                GROUP BY i.identity_id
                """,
                (identity_id,),
            ).fetchone()
        return None if row is None else self._row_to_public(row)

    def delete(self, identity_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM identities WHERE identity_id = ?", (identity_id,))
            return cursor.rowcount > 0

    def prototypes(self) -> dict[str, tuple[dict[str, Any], np.ndarray]]:
        identities = {item["identity_id"]: item for item in self.list_identities()}
        result: dict[str, tuple[dict[str, Any], np.ndarray]] = {}
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT identity_id, embedding, dimensions, quality_score
                FROM identity_samples
                ORDER BY identity_id, quality_score DESC, created_at_unix DESC
                """
            ).fetchall()
        grouped: dict[str, list[np.ndarray]] = {}
        for row in rows:
            identity_id = str(row["identity_id"])
            if identity_id not in identities:
                continue
            vector = np.frombuffer(row["embedding"], dtype=np.float32, count=int(row["dimensions"])).copy()
            if vector.size:
                grouped.setdefault(identity_id, []).append(normalize_embedding(vector))
        for identity_id, samples in grouped.items():
            dimensions = {sample.size for sample in samples}
            if len(dimensions) != 1:
                continue
            prototype = normalize_embedding(np.mean(np.stack(samples, axis=0), axis=0))
            result[identity_id] = (identities[identity_id], prototype)
        return result

    @staticmethod
    def _row_to_public(row: sqlite3.Row) -> dict[str, Any]:
        try:
            metadata = json.loads(str(row["metadata_json"]) or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return {
            "identity_id": str(row["identity_id"]),
            "display_name": str(row["display_name"]),
            "external_id": row["external_id"],
            "consent_at_unix": float(row["consent_at_unix"]),
            "created_at_unix": float(row["created_at_unix"]),
            "updated_at_unix": float(row["updated_at_unix"]),
            "metadata": metadata,
            "sample_count": int(row["sample_count"]),
        }


class IdentityRuntime:
    def __init__(self, settings: IdentitySettings, server_root: Path) -> None:
        self.settings = settings
        model = Path(settings.model_path)
        database = Path(settings.database_path)
        self.model_path = model if model.is_absolute() else server_root / model
        self.database_path = database if database.is_absolute() else server_root / database
        self.store = IdentityStore(self.database_path, settings.max_samples_per_identity)
        self.recognizer: Any | None = None
        self.last_error: str | None = None
        self.embedding_count = 0
        self.last_embedding_ms: float | None = None
        self._prototypes: dict[str, tuple[dict[str, Any], np.ndarray]] = {}
        self._track_results: dict[int, dict[str, Any]] = {}
        self._track_embeddings: dict[int, np.ndarray] = {}
        self._track_embedding_quality: dict[int, float] = {}
        self._candidates: dict[int, tuple[str | None, int]] = {}
        self._last_attempt: dict[int, float] = {}
        self._lock = threading.RLock()

    @property
    def ready(self) -> bool:
        return not self.settings.enabled or self.recognizer is not None

    def load(self) -> None:
        self.reload_gallery()
        if not self.settings.enabled:
            return
        if not self.model_path.exists():
            raise IdentityRuntimeError(
                f"SFace model is missing: {self.model_path}. Run scripts/download_face_models.ps1."
            )
        try:
            import cv2

            if not hasattr(cv2, "FaceRecognizerSF"):
                raise RuntimeError(f"OpenCV {cv2.__version__} does not provide FaceRecognizerSF")
            self.recognizer = cv2.FaceRecognizerSF.create(str(self.model_path), "")
            self.last_error = None
        except Exception as exc:
            self.recognizer = None
            raise IdentityRuntimeError(f"SFace initialization failed: {type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self.recognizer = None
            self._track_results.clear()
            self._track_embeddings.clear()
            self._track_embedding_quality.clear()
            self._candidates.clear()
            self._last_attempt.clear()

    def reload_gallery(self) -> None:
        with self._lock:
            self._prototypes = self.store.prototypes()

    def extract_embedding(self, source_frame: np.ndarray, face_row: np.ndarray) -> np.ndarray:
        if self.recognizer is None:
            raise IdentityRuntimeError("SFace recognizer is not loaded")
        started = time.perf_counter()
        aligned = self.recognizer.alignCrop(source_frame, np.asarray(face_row, dtype=np.float32))
        feature = self.recognizer.feature(aligned)
        embedding = normalize_embedding(np.asarray(feature, dtype=np.float32))
        self.embedding_count += 1
        self.last_embedding_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return embedding

    def process_track(
        self,
        *,
        track_id: int,
        source_frame: np.ndarray,
        face_row: np.ndarray,
        quality_score: float,
        now_monotonic: float,
    ) -> tuple[dict[str, Any] | None, tuple[str, dict[str, Any]] | None]:
        if not self.settings.enabled or self.recognizer is None:
            return None, None
        if now_monotonic - self._last_attempt.get(track_id, 0.0) < self.settings.recognition_interval_seconds:
            return self.result_for_track(track_id), None
        self._last_attempt[track_id] = now_monotonic
        embedding = self.extract_embedding(source_frame, face_row)
        with self._lock:
            self._track_embeddings[track_id] = embedding.copy()
            self._track_embedding_quality[track_id] = float(quality_score)
        return self.match_embedding(
            track_id=track_id,
            embedding=embedding,
            quality_score=quality_score,
            now_monotonic=now_monotonic,
        )

    def match_embedding(
        self,
        *,
        track_id: int,
        embedding: np.ndarray,
        quality_score: float,
        now_monotonic: float,
    ) -> tuple[dict[str, Any] | None, tuple[str, dict[str, Any]] | None]:
        vector = normalize_embedding(embedding)
        with self._lock:
            scored: list[tuple[float, str, dict[str, Any]]] = []
            for identity_id, (identity, prototype) in self._prototypes.items():
                if prototype.size == vector.size:
                    scored.append((cosine_similarity(vector, prototype), identity_id, identity))
            scored.sort(reverse=True, key=lambda item: item[0])
            best_score, best_id, best_identity = scored[0] if scored else (-1.0, "", {})
            second_score = scored[1][0] if len(scored) > 1 else -1.0
            margin = best_score - second_score
            eligible = bool(
                best_id
                and best_score >= self.settings.cosine_threshold
                and margin >= self.settings.minimum_margin
            )
            candidate_id = best_id if eligible else None
            previous_candidate, count = self._candidates.get(track_id, (None, 0))
            count = count + 1 if previous_candidate == candidate_id else 1
            self._candidates[track_id] = (candidate_id, count)

            previous_result = self._track_results.get(track_id)
            if candidate_id is None or count < self.settings.confirm_observations:
                if previous_result is None:
                    return None, None
                return dict(previous_result), None

            result = {
                "track_id": int(track_id),
                "identity_id": candidate_id,
                "display_name": best_identity["display_name"],
                "external_id": best_identity.get("external_id"),
                "similarity": round(best_score, 6),
                "second_best_similarity": round(second_score, 6),
                "margin": round(margin, 6),
                "quality_score": round(float(quality_score), 6),
                "confirmed_observations": int(count),
                "identified_at_unix": time.time(),
                "consent_at_unix": best_identity["consent_at_unix"],
            }
            changed = previous_result is None or previous_result.get("identity_id") != candidate_id
            self._track_results[track_id] = result
            if not changed:
                return dict(result), None
            return dict(result), ("visitor_identified", dict(result))

    def enroll(
        self,
        *,
        track_id: int,
        display_name: str,
        consent: bool,
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        identity_id: str | None = None,
    ) -> dict[str, Any]:
        if self.settings.require_explicit_consent and not consent:
            raise ValueError("explicit consent is required for identity enrollment")
        with self._lock:
            cached = self._track_embeddings.get(track_id)
            quality_score = self._track_embedding_quality.get(track_id, 0.0)
            embedding = None if cached is None else cached.copy()
        if embedding is None:
            raise ValueError("no current high-quality face embedding is available for this track")
        if quality_score < self.settings.enrollment_min_quality:
            raise ValueError(
                f"face quality {quality_score:.3f} is below enrollment minimum "
                f"{self.settings.enrollment_min_quality:.3f}"
            )
        identity = self.store.enroll(
            display_name=display_name,
            embedding=embedding,
            quality_score=quality_score,
            consent_at_unix=time.time(),
            external_id=external_id,
            metadata=metadata,
            identity_id=identity_id,
        )
        self.reload_gallery()
        with self._lock:
            self._track_results[track_id] = {
                "track_id": track_id,
                "identity_id": identity["identity_id"],
                "display_name": identity["display_name"],
                "external_id": identity.get("external_id"),
                "similarity": 1.0,
                "second_best_similarity": -1.0,
                "margin": 2.0,
                "quality_score": round(float(quality_score), 6),
                "confirmed_observations": self.settings.confirm_observations,
                "identified_at_unix": time.time(),
                "consent_at_unix": identity["consent_at_unix"],
            }
        return identity

    def delete_identity(self, identity_id: str) -> bool:
        deleted = self.store.delete(identity_id)
        if deleted:
            self.reload_gallery()
            with self._lock:
                self._track_results = {
                    track_id: result
                    for track_id, result in self._track_results.items()
                    if result.get("identity_id") != identity_id
                }
        return deleted

    def cleanup_tracks(self, active_track_ids: set[int]) -> None:
        with self._lock:
            for mapping in (
                self._track_results,
                self._track_embeddings,
                self._track_embedding_quality,
                self._candidates,
                self._last_attempt,
            ):
                for track_id in list(mapping):
                    if track_id not in active_track_ids:
                        mapping.pop(track_id, None)

    def result_for_track(self, track_id: int) -> dict[str, Any] | None:
        with self._lock:
            result = self._track_results.get(int(track_id))
            return None if result is None else dict(result)

    def enrich_tracks(self, tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        with self._lock:
            for track in tracks:
                item = dict(track)
                identity = self._track_results.get(int(track["track_id"]))
                item["identity"] = None if identity is None else dict(identity)
                enriched.append(item)
        return enriched

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            recognized = [dict(result) for result in self._track_results.values()]
            gallery_count = len(self._prototypes)
        recognized.sort(key=lambda item: item["track_id"])
        return {
            "enabled": self.settings.enabled,
            "ready": self.ready,
            "model_path": str(self.model_path),
            "model_exists": self.model_path.exists(),
            "database_path": str(self.database_path),
            "identity_count": gallery_count,
            "identities": self.store.list_identities(),
            "recognized_tracks": recognized,
            "embedding_count": self.embedding_count,
            "last_embedding_ms": self.last_embedding_ms,
            "cosine_threshold": self.settings.cosine_threshold,
            "minimum_margin": self.settings.minimum_margin,
            "last_error": self.last_error,
            "privacy": {
                "automatic_enrollment": False,
                "stores_face_images": False,
                "requires_explicit_consent": self.settings.require_explicit_consent,
            },
        }
