from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.config import VisitorSessionSettings

PROVISIONAL_REBIND_SECONDS = 5.0


def _normalize(value: np.ndarray | None) -> np.ndarray | None:
    if value is None:
        return None
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return None
    return vector / norm


def _similarity(left: np.ndarray | None, right: np.ndarray | None) -> float:
    if left is None or right is None or left.size != right.size:
        return -1.0
    return float(np.clip(np.dot(left, right), -1.0, 1.0))


def _center(track: dict[str, Any]) -> tuple[float, float]:
    box = track.get("bbox") or {}
    return (
        float(box.get("x", 0.0)) + float(box.get("width", 0.0)) / 2.0,
        float(box.get("y", 0.0)) + float(box.get("height", 0.0)) / 2.0,
    )


@dataclass
class VisitorSession:
    visitor_session_id: str
    created_at_monotonic: float
    last_seen_monotonic: float
    active_track_id: int | None
    last_track_id: int
    last_center: tuple[float, float]
    track_ids: list[int] = field(default_factory=list)
    face_embeddings: deque[np.ndarray] = field(default_factory=deque)
    body_embeddings: deque[np.ndarray] = field(default_factory=deque)
    identity: dict[str, Any] | None = None
    primary: bool = False
    recovery_count: int = 0
    last_recovery_reason: str | None = None

    def best_face_similarity(self, query: np.ndarray | None) -> float:
        if query is None:
            return -1.0
        return max(
            (_similarity(query, item) for item in self.face_embeddings),
            default=-1.0,
        )

    def best_body_similarity(self, query: np.ndarray | None) -> float:
        if query is None:
            return -1.0
        return max(
            (_similarity(query, item) for item in self.body_embeddings),
            default=-1.0,
        )

    def update_visible(
        self,
        *,
        track: dict[str, Any],
        face_embedding: np.ndarray | None,
        body_embedding: np.ndarray | None,
        identity: dict[str, Any] | None,
        now: float,
        settings: VisitorSessionSettings,
    ) -> None:
        track_id = int(track["track_id"])
        self.active_track_id = track_id
        self.last_track_id = track_id
        self.last_seen_monotonic = now
        self.last_center = _center(track)
        self.primary = bool(track.get("primary"))
        if track_id not in self.track_ids:
            self.track_ids.append(track_id)
        normalized_face = _normalize(face_embedding)
        if normalized_face is not None:
            self.face_embeddings.append(normalized_face)
            while len(self.face_embeddings) > settings.max_face_embeddings:
                self.face_embeddings.popleft()
        normalized_body = _normalize(body_embedding)
        if normalized_body is not None:
            self.body_embeddings.append(normalized_body)
            while len(self.body_embeddings) > settings.max_body_embeddings:
                self.body_embeddings.popleft()
        if identity is not None:
            self.identity = dict(identity)

    def public(self, now: float) -> dict[str, Any]:
        return {
            "visitor_session_id": self.visitor_session_id,
            "active_track_id": self.active_track_id,
            "last_track_id": self.last_track_id,
            "track_ids": list(self.track_ids),
            "age_seconds": round(max(now - self.created_at_monotonic, 0.0), 3),
            "last_seen_age_seconds": round(
                max(now - self.last_seen_monotonic, 0.0), 3
            ),
            "face_embedding_count": len(self.face_embeddings),
            "body_embedding_count": len(self.body_embeddings),
            "identity": None if self.identity is None else dict(self.identity),
            "primary": self.primary,
            "recovery_count": self.recovery_count,
            "last_recovery_reason": self.last_recovery_reason,
        }


class VisitorSessionRuntime:
    """Memory-only bridge between short MOT tracks and a stable visitor session."""

    def __init__(self, settings: VisitorSessionSettings) -> None:
        self.settings = settings
        self.sessions: dict[str, VisitorSession] = {}
        self.track_to_session: dict[int, str] = {}
        self._pending_matches: dict[int, tuple[str | None, int, str | None]] = {}
        self._lock = threading.RLock()
        self.recovery_count = 0
        self.expired_count = 0

    @property
    def ready(self) -> bool:
        return True

    def reset(self) -> None:
        with self._lock:
            self.sessions.clear()
            self.track_to_session.clear()
            self._pending_matches.clear()
            self.recovery_count = 0
            self.expired_count = 0

    def update(
        self,
        tracks: list[dict[str, Any]],
        *,
        face_embeddings: dict[int, np.ndarray | None],
        body_embeddings: dict[int, np.ndarray | None],
        identities: dict[int, dict[str, Any] | None],
        now: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
        now = time.monotonic() if now is None else float(now)
        events: list[tuple[str, dict[str, Any]]] = []
        if not self.settings.enabled:
            return [dict(track) for track in tracks], events

        with self._lock:
            visible_tracks = [
                dict(track)
                for track in tracks
                if bool(track.get("visible")) and track.get("state") == "confirmed"
            ]
            visible_track_ids = {int(track["track_id"]) for track in visible_tracks}
            for session in self.sessions.values():
                if session.active_track_id not in visible_track_ids:
                    session.active_track_id = None

            assigned_sessions: set[str] = set()
            by_track: dict[int, dict[str, Any]] = {
                int(track["track_id"]): dict(track) for track in tracks
            }
            visible_tracks.sort(
                key=lambda item: (
                    not bool(item.get("primary")),
                    -float(item.get("area_ratio", 0.0)),
                )
            )

            for item in visible_tracks:
                track_id = int(item["track_id"])
                face = _normalize(face_embeddings.get(track_id))
                body = _normalize(body_embeddings.get(track_id))
                identity = identities.get(track_id)
                mapped_id = self.track_to_session.get(track_id)
                session = self.sessions.get(mapped_id or "")

                if session is not None and self._is_provisional(session, track_id, now):
                    candidate, reason, diagnostics = self._match_session(
                        track=item,
                        face_embedding=face,
                        body_embedding=body,
                        identity=identity,
                        now=now,
                        assigned_sessions=assigned_sessions,
                        excluded_session_ids={session.visitor_session_id},
                    )
                    if candidate is not None:
                        provisional_id = session.visitor_session_id
                        previous_track_id = candidate.last_track_id
                        self._discard_session(provisional_id)
                        session = candidate
                        self.track_to_session[track_id] = session.visitor_session_id
                        session.recovery_count += 1
                        session.last_recovery_reason = reason
                        self.recovery_count += 1
                        events.append(
                            (
                                "visitor_session_recovered",
                                {
                                    "visitor_session_id": session.visitor_session_id,
                                    "previous_track_id": previous_track_id,
                                    "current_track_id": track_id,
                                    "reason": reason,
                                    "replaced_provisional_session_id": provisional_id,
                                    **diagnostics,
                                },
                            )
                        )

                if session is None:
                    session, reason, diagnostics = self._match_session(
                        track=item,
                        face_embedding=face,
                        body_embedding=body,
                        identity=identity,
                        now=now,
                        assigned_sessions=assigned_sessions,
                        excluded_session_ids=set(),
                    )
                    if session is None:
                        session = self._create_session(item, now)
                        events.append(
                            (
                                "visitor_session_started",
                                {
                                    "visitor_session_id": session.visitor_session_id,
                                    "track_id": track_id,
                                },
                            )
                        )
                    else:
                        previous_track_id = session.last_track_id
                        session.recovery_count += 1
                        session.last_recovery_reason = reason
                        self.recovery_count += 1
                        events.append(
                            (
                                "visitor_session_recovered",
                                {
                                    "visitor_session_id": session.visitor_session_id,
                                    "previous_track_id": previous_track_id,
                                    "current_track_id": track_id,
                                    "reason": reason,
                                    **diagnostics,
                                },
                            )
                        )
                    self.track_to_session[track_id] = session.visitor_session_id

                previous_identity_id = (
                    None
                    if session.identity is None
                    else session.identity.get("identity_id")
                )
                session.update_visible(
                    track=item,
                    face_embedding=face,
                    body_embedding=body,
                    identity=identity,
                    now=now,
                    settings=self.settings,
                )
                assigned_sessions.add(session.visitor_session_id)
                if (
                    session.identity is not None
                    and session.identity.get("identity_id") != previous_identity_id
                ):
                    events.append(
                        (
                            "visitor_session_identified",
                            {
                                "visitor_session_id": session.visitor_session_id,
                                "track_id": track_id,
                                "identity_id": session.identity.get("identity_id"),
                                "display_name": session.identity.get("display_name"),
                            },
                        )
                    )
                by_track[track_id] = self._enrich_track(item, session, now)

            for track_id, item in list(by_track.items()):
                if track_id in visible_track_ids:
                    continue
                mapped_id = self.track_to_session.get(track_id)
                session = self.sessions.get(mapped_id or "")
                if session is not None:
                    by_track[track_id] = self._enrich_track(item, session, now)

            for session in self._expire(now):
                events.append(
                    (
                        "visitor_session_expired",
                        {
                            "visitor_session_id": session.visitor_session_id,
                            "last_track_id": session.last_track_id,
                            "identity_id": (
                                None
                                if session.identity is None
                                else session.identity.get("identity_id")
                            ),
                        },
                    )
                )
            return [by_track[key] for key in sorted(by_track)], events

    def _is_provisional(
        self, session: VisitorSession, track_id: int, now: float
    ) -> bool:
        return bool(
            session.track_ids == [track_id]
            and now - session.created_at_monotonic <= PROVISIONAL_REBIND_SECONDS
        )

    def _enrich_track(
        self, track: dict[str, Any], session: VisitorSession, now: float
    ) -> dict[str, Any]:
        item = dict(track)
        item["visitor_session_id"] = session.visitor_session_id
        item["visitor_session"] = session.public(now)
        if item.get("identity") is None and session.identity is not None:
            item["identity"] = dict(session.identity)
            item["identity_source"] = "visitor_session_memory"
        return item

    def _create_session(self, track: dict[str, Any], now: float) -> VisitorSession:
        track_id = int(track["track_id"])
        session = VisitorSession(
            visitor_session_id=f"visitor_{uuid.uuid4().hex[:12]}",
            created_at_monotonic=now,
            last_seen_monotonic=now,
            active_track_id=track_id,
            last_track_id=track_id,
            last_center=_center(track),
            track_ids=[track_id],
            face_embeddings=deque(maxlen=self.settings.max_face_embeddings),
            body_embeddings=deque(maxlen=self.settings.max_body_embeddings),
            primary=bool(track.get("primary")),
        )
        self.sessions[session.visitor_session_id] = session
        return session

    def _confirm_candidate(
        self,
        *,
        track_id: int,
        session: VisitorSession,
        reason: str,
        required: int,
    ) -> VisitorSession | None:
        previous_id, count, previous_reason = self._pending_matches.get(
            track_id, (None, 0, None)
        )
        same = previous_id == session.visitor_session_id and previous_reason == reason
        count = count + 1 if same else 1
        self._pending_matches[track_id] = (
            session.visitor_session_id,
            count,
            reason,
        )
        if count < required:
            return None
        self._pending_matches.pop(track_id, None)
        return session

    def _match_session(
        self,
        *,
        track: dict[str, Any],
        face_embedding: np.ndarray | None,
        body_embedding: np.ndarray | None,
        identity: dict[str, Any] | None,
        now: float,
        assigned_sessions: set[str],
        excluded_session_ids: set[str],
    ) -> tuple[VisitorSession | None, str | None, dict[str, Any]]:
        track_id = int(track["track_id"])
        candidates = [
            session
            for session in self.sessions.values()
            if now - session.last_seen_monotonic <= self.settings.ttl_seconds
            and session.visitor_session_id not in assigned_sessions
            and session.visitor_session_id not in excluded_session_ids
            and session.active_track_id is None
        ]
        identity_id = None if identity is None else identity.get("identity_id")
        if identity_id:
            identity_matches = [
                session
                for session in candidates
                if session.identity is not None
                and session.identity.get("identity_id") == identity_id
            ]
            if identity_matches:
                selected = max(
                    identity_matches, key=lambda item: item.last_seen_monotonic
                )
                self._pending_matches.pop(track_id, None)
                return selected, "registered_identity", {"identity_id": identity_id}

        scored: list[tuple[float, float, float, VisitorSession]] = []
        current_center = np.asarray(_center(track), dtype=np.float32)
        for session in candidates:
            face_score = session.best_face_similarity(face_embedding)
            body_score = session.best_body_similarity(body_embedding)
            center_distance = float(
                np.linalg.norm(
                    current_center - np.asarray(session.last_center, dtype=np.float32)
                )
            )
            scored.append((face_score, body_score, center_distance, session))
        scored.sort(key=lambda row: (row[0], row[1], -row[2]), reverse=True)
        if not scored:
            self._pending_matches.pop(track_id, None)
            return None, None, {}

        best_face, best_body, center_distance, best_session = scored[0]
        second_face = scored[1][0] if len(scored) > 1 else -1.0
        second_body = scored[1][1] if len(scored) > 1 else -1.0
        face_margin = best_face - second_face
        body_margin = best_body - second_body
        age = now - best_session.last_seen_monotonic
        diagnostics = {
            "face_similarity": round(best_face, 6),
            "face_margin": round(face_margin, 6),
            "body_similarity": round(best_body, 6),
            "body_margin": round(body_margin, 6),
            "age_seconds": round(age, 3),
            "center_distance": round(center_distance, 6),
        }

        if (
            best_face >= self.settings.face_high_similarity
            and face_margin >= self.settings.face_minimum_margin
        ):
            self._pending_matches.pop(track_id, None)
            return best_session, "face_high", diagnostics

        if (
            best_face >= self.settings.face_medium_similarity
            and face_margin >= self.settings.face_minimum_margin
            and (
                best_body >= self.settings.body_medium_similarity
                or center_distance <= self.settings.max_center_distance
            )
        ):
            confirmed = self._confirm_candidate(
                track_id=track_id,
                session=best_session,
                reason="face_body_medium",
                required=self.settings.medium_confirmations,
            )
            return confirmed, "face_body_medium", diagnostics

        if (
            face_embedding is None
            and age <= self.settings.body_only_max_age_seconds
            and best_body >= self.settings.body_high_similarity
            and body_margin >= self.settings.body_minimum_margin
            and center_distance <= self.settings.max_center_distance
        ):
            confirmed = self._confirm_candidate(
                track_id=track_id,
                session=best_session,
                reason="body_only",
                required=self.settings.body_only_confirmations,
            )
            return confirmed, "body_only", diagnostics

        self._pending_matches.pop(track_id, None)
        return None, None, diagnostics

    def _discard_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        for track_id, mapped_id in list(self.track_to_session.items()):
            if mapped_id == session_id:
                self.track_to_session.pop(track_id, None)
                self._pending_matches.pop(track_id, None)

    def _expire(self, now: float) -> list[VisitorSession]:
        expired: list[VisitorSession] = []
        for session_id, session in list(self.sessions.items()):
            if now - session.last_seen_monotonic <= self.settings.ttl_seconds:
                continue
            expired.append(session)
            self._discard_session(session_id)
        self.expired_count += len(expired)
        return expired

    def session_for_track(self, track_id: int) -> dict[str, Any] | None:
        with self._lock:
            session_id = self.track_to_session.get(int(track_id))
            session = self.sessions.get(session_id or "")
            return None if session is None else session.public(time.monotonic())

    def identity_for_track(self, track_id: int) -> dict[str, Any] | None:
        with self._lock:
            session_id = self.track_to_session.get(int(track_id))
            session = self.sessions.get(session_id or "")
            if session is None or session.identity is None:
                return None
            return dict(session.identity)

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            sessions = [session.public(now) for session in self.sessions.values()]
            sessions.sort(key=lambda item: item["visitor_session_id"])
            return {
                "enabled": self.settings.enabled,
                "ready": self.ready,
                "ttl_seconds": self.settings.ttl_seconds,
                "session_count": len(sessions),
                "active_session_count": sum(
                    1 for item in sessions if item["active_track_id"] is not None
                ),
                "recovery_count": self.recovery_count,
                "expired_count": self.expired_count,
                "pending_match_count": len(self._pending_matches),
                "sessions": sessions,
                "privacy": {
                    "stores_images": False,
                    "persists_anonymous_embeddings": False,
                    "memory_only": True,
                },
            }
