from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections import deque
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
        metadata_json = json.dumps(
            metadata or {}, ensure_ascii=False, separators=(",", ":")
        )
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT identity_id FROM identities WHERE identity_id = ?",
                (identity_id,),
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
                INSERT INTO identity_samples(
                    identity_id, embedding, dimensions, quality_score, created_at_unix
                ) VALUES (?, ?, ?, ?, ?)
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
                    "DELETE FROM identity_samples WHERE sample_id = ?",
                    (int(row["sample_id"]),),
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

    def find_by_display_name(self, display_name: str) -> dict[str, Any] | None:
        clean = display_name.strip().casefold()
        if not clean:
            return None
        matches = [
            item for item in self.list_identities() if item["display_name"].casefold() == clean
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: (item["created_at_unix"], item["identity_id"]))
        return matches[0]

    def delete(self, identity_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM identities WHERE identity_id = ?", (identity_id,)
            )
            return cursor.rowcount > 0

    def gallery_samples(
        self,
    ) -> dict[str, tuple[dict[str, Any], list[tuple[np.ndarray, float]]]]:
        identities = {item["identity_id"]: item for item in self.list_identities()}
        grouped: dict[str, list[tuple[np.ndarray, float]]] = {}
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT identity_id, embedding, dimensions, quality_score
                FROM identity_samples
                ORDER BY identity_id, quality_score DESC, created_at_unix DESC
                """
            ).fetchall()
        for row in rows:
            identity_id = str(row["identity_id"])
            if identity_id not in identities:
                continue
            vector = np.frombuffer(
                row["embedding"], dtype=np.float32, count=int(row["dimensions"])
            ).copy()
            if vector.size:
                grouped.setdefault(identity_id, []).append(
                    (normalize_embedding(vector), float(row["quality_score"]))
                )
        return {
            identity_id: (identities[identity_id], samples)
            for identity_id, samples in grouped.items()
            if samples
        }

    def prototypes(self) -> dict[str, tuple[dict[str, Any], np.ndarray]]:
        result: dict[str, tuple[dict[str, Any], np.ndarray]] = {}
        for identity_id, (identity, samples) in self.gallery_samples().items():
            dimensions = {sample[0].size for sample in samples}
            if len(dimensions) != 1:
                continue
            prototype = normalize_embedding(
                np.mean(np.stack([sample[0] for sample in samples], axis=0), axis=0)
            )
            result[identity_id] = (identity, prototype)
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
        self.store = IdentityStore(
            self.database_path, settings.max_samples_per_identity
        )
        self.recognizer: Any | None = None
        self.last_error: str | None = None
        self.embedding_count = 0
        self.last_embedding_ms: float | None = None

        # _gallery is grouped by case-folded display name so duplicate Rico rows do not compete.
        self._gallery: dict[str, dict[str, Any]] = {}
        self._duplicate_groups: list[dict[str, Any]] = []
        self._prototypes: dict[str, tuple[dict[str, Any], np.ndarray]] = {}
        self._track_results: dict[int, dict[str, Any]] = {}
        self._track_embeddings: dict[int, np.ndarray] = {}
        self._track_embedding_quality: dict[int, float] = {}
        self._track_diagnostics: dict[int, dict[str, Any]] = {}
        self._recent_samples: dict[int, deque[dict[str, Any]]] = {}
        self._candidates: dict[int, tuple[str | None, int]] = {}
        self._last_attempt: dict[int, float] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    @property
    def ready(self) -> bool:
        return not self.settings.enabled or self.recognizer is not None

    def load(self) -> None:
        self.reload_gallery()
        if not self.settings.enabled:
            return
        if not self.model_path.exists():
            raise IdentityRuntimeError(
                f"SFace model is missing: {self.model_path}. "
                "Run scripts/download_face_models.ps1."
            )
        try:
            import cv2

            if not hasattr(cv2, "FaceRecognizerSF"):
                raise RuntimeError(
                    f"OpenCV {cv2.__version__} does not provide FaceRecognizerSF"
                )
            self.recognizer = cv2.FaceRecognizerSF.create(str(self.model_path), "")
            self.last_error = None
        except Exception as exc:
            self.recognizer = None
            raise IdentityRuntimeError(
                f"SFace initialization failed: {type(exc).__name__}: {exc}"
            ) from exc

    def close(self) -> None:
        with self._condition:
            self.recognizer = None
            self._track_results.clear()
            self._track_embeddings.clear()
            self._track_embedding_quality.clear()
            self._track_diagnostics.clear()
            self._recent_samples.clear()
            self._candidates.clear()
            self._last_attempt.clear()
            self._condition.notify_all()

    def reload_gallery(self) -> None:
        raw = self.store.gallery_samples()
        identities = self.store.list_identities()
        grouped: dict[str, dict[str, Any]] = {}
        duplicate_groups: list[dict[str, Any]] = []
        for identity in identities:
            key = identity["display_name"].strip().casefold()
            group = grouped.setdefault(
                key,
                {
                    "identities": [],
                    "samples": [],
                    "canonical": None,
                },
            )
            group["identities"].append(identity)
            identity_samples = raw.get(identity["identity_id"], (identity, []))[1]
            group["samples"].extend(identity_samples)
        for key, group in grouped.items():
            group["identities"].sort(
                key=lambda item: (item["created_at_unix"], item["identity_id"])
            )
            group["canonical"] = group["identities"][0]
            if len(group["identities"]) > 1:
                duplicate_groups.append(
                    {
                        "display_name": group["canonical"]["display_name"],
                        "canonical_identity_id": group["canonical"]["identity_id"],
                        "identity_ids": [
                            item["identity_id"] for item in group["identities"]
                        ],
                        "pooled_sample_count": len(group["samples"]),
                    }
                )
        with self._lock:
            self._gallery = grouped
            self._duplicate_groups = duplicate_groups
            self._prototypes = self.store.prototypes()

    def extract_embedding(
        self, source_frame: np.ndarray, face_row: np.ndarray
    ) -> np.ndarray:
        if self.recognizer is None:
            raise IdentityRuntimeError("SFace recognizer is not loaded")
        started = time.perf_counter()
        aligned = self.recognizer.alignCrop(
            source_frame, np.asarray(face_row[:14], dtype=np.float32)
        )
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
        enrollment_candidate: bool = False,
        face_diagnostic: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, tuple[str, dict[str, Any]] | None]:
        if not self.settings.enabled or self.recognizer is None:
            return None, None
        if (
            now_monotonic - self._last_attempt.get(track_id, 0.0)
            < self.settings.recognition_interval_seconds
        ):
            return self.result_for_track(track_id), None
        self._last_attempt[track_id] = now_monotonic
        embedding = self.extract_embedding(source_frame, face_row)
        self.record_embedding_sample(
            track_id=track_id,
            embedding=embedding,
            quality_score=quality_score,
            enrollment_candidate=enrollment_candidate,
            now_monotonic=now_monotonic,
            diagnostic=face_diagnostic,
        )
        return self.match_embedding(
            track_id=track_id,
            embedding=embedding,
            quality_score=quality_score,
            now_monotonic=now_monotonic,
        )

    def record_embedding_sample(
        self,
        *,
        track_id: int,
        embedding: np.ndarray,
        quality_score: float,
        enrollment_candidate: bool,
        now_monotonic: float | None = None,
        diagnostic: dict[str, Any] | None = None,
    ) -> None:
        vector = normalize_embedding(embedding)
        timestamp = time.monotonic() if now_monotonic is None else float(now_monotonic)
        with self._condition:
            self._track_embeddings[int(track_id)] = vector.copy()
            self._track_embedding_quality[int(track_id)] = float(quality_score)
            if diagnostic is not None:
                self._track_diagnostics[int(track_id)] = dict(diagnostic)
            queue = self._recent_samples.setdefault(
                int(track_id), deque(maxlen=self.settings.recent_samples_per_track)
            )
            queue.append(
                {
                    "embedding": vector.copy(),
                    "quality_score": float(quality_score),
                    "enrollment_candidate": bool(enrollment_candidate),
                    "captured_at_monotonic": timestamp,
                }
            )
            self._condition.notify_all()

    def _score_gallery(
        self, vector: np.ndarray
    ) -> list[tuple[float, str, dict[str, Any], list[float]]]:
        scored: list[tuple[float, str, dict[str, Any], list[float]]] = []
        if self._gallery:
            for key, group in self._gallery.items():
                sample_scores: list[tuple[float, float]] = []
                for sample, quality in group["samples"]:
                    if sample.size == vector.size:
                        sample_scores.append(
                            (cosine_similarity(vector, sample), float(quality))
                        )
                if not sample_scores:
                    continue
                sample_scores.sort(reverse=True, key=lambda item: item[0])
                top = sample_scores[: self.settings.top_k_samples]
                weights = [0.5 + 0.5 * max(0.0, min(1.0, quality)) for _, quality in top]
                weighted = sum(score * weight for (score, _), weight in zip(top, weights))
                combined = weighted / max(sum(weights), 1e-9)
                scored.append(
                    (
                        float(combined),
                        key,
                        dict(group["canonical"]),
                        [round(item[0], 6) for item in top],
                    )
                )
        elif self._prototypes:
            # Backward-compatible path used by focused unit tests.
            for identity_id, (identity, prototype) in self._prototypes.items():
                if prototype.size == vector.size:
                    score = cosine_similarity(vector, prototype)
                    scored.append((score, identity_id, dict(identity), [round(score, 6)]))
        scored.sort(reverse=True, key=lambda item: item[0])
        return scored

    def _decision(self, best_score: float, margin: float) -> tuple[bool, str, int]:
        if not self.settings.adaptive_confirmation_enabled:
            eligible = (
                best_score >= self.settings.cosine_threshold
                and margin >= self.settings.minimum_margin
            )
            return eligible, "fixed", self.settings.confirm_observations
        if (
            best_score >= self.settings.high_similarity_threshold
            and margin >= self.settings.high_minimum_margin
        ):
            return True, "high", self.settings.high_confirm_observations
        if (
            best_score >= self.settings.medium_similarity_threshold
            and margin >= self.settings.medium_minimum_margin
        ):
            return True, "medium", self.settings.medium_confirm_observations
        if (
            best_score >= self.settings.low_similarity_threshold
            and margin >= self.settings.low_minimum_margin
        ):
            return True, "low", self.settings.low_confirm_observations
        return False, "rejected", self.settings.low_confirm_observations

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
            scored = self._score_gallery(vector)
            best_score, best_key, best_identity, best_sample_scores = (
                scored[0] if scored else (-1.0, "", {}, [])
            )
            second_score = scored[1][0] if len(scored) > 1 else -1.0
            margin = best_score - second_score
            eligible, tier, required = self._decision(best_score, margin)
            candidate_id = best_identity.get("identity_id") if eligible else None
            previous_candidate, count = self._candidates.get(track_id, (None, 0))
            count = count + 1 if previous_candidate == candidate_id else 1
            self._candidates[track_id] = (candidate_id, count)

            previous_result = self._track_results.get(track_id)
            if candidate_id is None or count < required:
                if previous_result is None:
                    return None, None
                return dict(previous_result), None

            result = {
                "track_id": int(track_id),
                "identity_id": candidate_id,
                "display_name": best_identity["display_name"],
                "external_id": best_identity.get("external_id"),
                "similarity": round(best_score, 6),
                "sample_similarities": best_sample_scores,
                "second_best_similarity": round(second_score, 6),
                "margin": round(margin, 6),
                "quality_score": round(float(quality_score), 6),
                "decision_tier": tier,
                "required_observations": int(required),
                "confirmed_observations": int(count),
                "identified_at_unix": time.time(),
                "consent_at_unix": best_identity["consent_at_unix"],
                "source": "sface_gallery",
            }
            changed = (
                previous_result is None
                or previous_result.get("identity_id") != candidate_id
            )
            self._track_results[track_id] = result
            if not changed:
                return dict(result), None
            return dict(result), ("visitor_identified", dict(result))

    def bind_identity_to_track(
        self,
        *,
        track_id: int,
        identity: dict[str, Any],
        source: str = "visitor_session_memory",
    ) -> dict[str, Any]:
        result = dict(identity)
        result.update(
            {
                "track_id": int(track_id),
                "source": source,
                "identified_at_unix": time.time(),
                "confirmed_observations": max(
                    int(result.get("confirmed_observations", 0)), 1
                ),
            }
        )
        with self._lock:
            self._track_results[int(track_id)] = result
        return dict(result)

    def _eligible_enrollment_samples(
        self, track_id: int, earliest: float
    ) -> list[dict[str, Any]]:
        queue = self._recent_samples.get(int(track_id), deque())
        return [
            item
            for item in queue
            if item["captured_at_monotonic"] >= earliest
            and item["enrollment_candidate"]
            and item["quality_score"] >= self.settings.enrollment_min_quality
        ]

    def _select_enrollment_samples(
        self, samples: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        ranked = sorted(samples, key=lambda item: item["quality_score"], reverse=True)
        selected: list[dict[str, Any]] = []
        for item in ranked:
            vector = item["embedding"]
            if selected and max(
                cosine_similarity(vector, existing["embedding"]) for existing in selected
            ) >= self.settings.enrollment_duplicate_similarity:
                continue
            selected.append(item)
            if len(selected) >= self.settings.enrollment_max_selected_samples:
                break
        if len(selected) < self.settings.enrollment_min_samples:
            for item in ranked:
                if item in selected:
                    continue
                selected.append(item)
                if len(selected) >= min(
                    self.settings.enrollment_max_selected_samples,
                    self.settings.enrollment_min_samples,
                ):
                    break
        return selected

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

        capture_started = time.monotonic()
        earliest = capture_started - self.settings.enrollment_prebuffer_seconds
        deadline = capture_started + self.settings.enrollment_capture_seconds
        with self._condition:
            while self.settings.enrollment_capture_seconds > 0:
                eligible = self._eligible_enrollment_samples(track_id, earliest)
                if len(eligible) >= self.settings.enrollment_target_samples:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(0.25, remaining))
            samples = self._eligible_enrollment_samples(track_id, earliest)
            if not samples:
                cached = self._track_embeddings.get(track_id)
                quality = self._track_embedding_quality.get(track_id, 0.0)
                if (
                    cached is not None
                    and quality >= self.settings.enrollment_min_quality
                    and self.settings.enrollment_capture_seconds <= 0
                ):
                    samples = [
                        {
                            "embedding": cached.copy(),
                            "quality_score": quality,
                            "enrollment_candidate": True,
                            "captured_at_monotonic": capture_started,
                        }
                    ]
            diagnostic = dict(self._track_diagnostics.get(track_id, {}))

        selected = self._select_enrollment_samples(samples)
        if len(selected) < self.settings.enrollment_min_samples:
            reasons = diagnostic.get("enrollment_rejection_reasons") or []
            reason_text = "; ".join(str(reason) for reason in reasons) or "no qualifying samples"
            raise ValueError(
                "enrollment capture did not collect enough usable faces: "
                f"{len(selected)}/{self.settings.enrollment_min_samples}; {reason_text}"
            )

        existing_result = self.result_for_track(track_id)
        resolved_identity_id = identity_id
        if resolved_identity_id is None and existing_result is not None:
            resolved_identity_id = existing_result.get("identity_id")
        if resolved_identity_id is None:
            existing_name = self.store.find_by_display_name(display_name)
            if existing_name is not None:
                resolved_identity_id = existing_name["identity_id"]
        reused_existing = resolved_identity_id is not None

        consent_at = time.time()
        identity: dict[str, Any] = {}
        for sample in selected:
            identity = self.store.enroll(
                display_name=display_name,
                embedding=sample["embedding"],
                quality_score=float(sample["quality_score"]),
                consent_at_unix=consent_at,
                external_id=external_id,
                metadata=metadata,
                identity_id=resolved_identity_id,
            )
            resolved_identity_id = identity["identity_id"]
        self.reload_gallery()
        best_quality = max(float(item["quality_score"]) for item in selected)
        result = {
            "track_id": int(track_id),
            "identity_id": identity["identity_id"],
            "display_name": identity["display_name"],
            "external_id": identity.get("external_id"),
            "similarity": 1.0,
            "sample_similarities": [1.0],
            "second_best_similarity": -1.0,
            "margin": 2.0,
            "quality_score": round(best_quality, 6),
            "decision_tier": "enrolled",
            "required_observations": 1,
            "confirmed_observations": 1,
            "identified_at_unix": time.time(),
            "consent_at_unix": identity["consent_at_unix"],
            "source": "explicit_enrollment",
        }
        with self._lock:
            self._track_results[int(track_id)] = result
        return {
            **identity,
            "samples_added": len(selected),
            "capture_seconds": round(time.monotonic() - capture_started, 3),
            "reused_existing_identity": reused_existing,
            "selected_quality_scores": [
                round(float(item["quality_score"]), 6) for item in selected
            ],
        }

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
                self._track_diagnostics,
                self._recent_samples,
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

    def embedding_for_track(self, track_id: int) -> np.ndarray | None:
        with self._lock:
            embedding = self._track_embeddings.get(int(track_id))
            return None if embedding is None else embedding.copy()

    def enrich_tracks(
        self, tracks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
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
            gallery_count = len(self._gallery)
            duplicate_groups = list(self._duplicate_groups)
        recognized.sort(key=lambda item: item["track_id"])
        return {
            "enabled": self.settings.enabled,
            "ready": self.ready,
            "model_path": str(self.model_path),
            "model_exists": self.model_path.exists(),
            "database_path": str(self.database_path),
            "identity_count": gallery_count,
            "database_identity_row_count": len(self.store.list_identities()),
            "identities": self.store.list_identities(),
            "duplicate_name_groups": duplicate_groups,
            "recognized_tracks": recognized,
            "embedding_count": self.embedding_count,
            "last_embedding_ms": self.last_embedding_ms,
            "cosine_threshold": self.settings.cosine_threshold,
            "minimum_margin": self.settings.minimum_margin,
            "adaptive_confirmation_enabled": self.settings.adaptive_confirmation_enabled,
            "last_error": self.last_error,
            "privacy": {
                "automatic_enrollment": False,
                "stores_face_images": False,
                "requires_explicit_consent": self.settings.require_explicit_consent,
            },
        }
