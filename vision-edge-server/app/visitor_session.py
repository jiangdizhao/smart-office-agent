from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.config import VisitorSessionSettings

VISIT_ABSENCE_SECONDS = 2.0


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
    assignment_stable: bool = True

    def best_face_similarity(self, query: np.ndarray | None) -> float:
        if query is None:
            return -1.0
        return max((_similarity(query, item) for item in self.face_embeddings), default=-1.0)

    def best_body_similarity(self, query: np.ndarray | None) -> float:
        if query is None:
            return -1.0
        return max((_similarity(query, item) for item in self.body_embeddings), default=-1.0)

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
        self.assignment_stable = True
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
        if identity is not None and identity.get("identity_id"):
            self.identity = dict(identity)

    def public(self, now: float) -> dict[str, Any]:
        return {
            "visitor_session_id": self.visitor_session_id,
            "visit_id": self.visitor_session_id,
            "active_track_id": self.active_track_id,
            "last_track_id": self.last_track_id,
            "track_ids": list(self.track_ids),
            "age_seconds": round(max(now - self.created_at_monotonic, 0.0), 3),
            "last_seen_age_seconds": round(max(now - self.last_seen_monotonic, 0.0), 3),
            "face_embedding_count": len(self.face_embeddings),
            "body_embedding_count": len(self.body_embeddings),
            "identity": None if self.identity is None else dict(self.identity),
            "primary": self.primary,
            "recovery_count": self.recovery_count,
            "last_recovery_reason": self.last_recovery_reason,
            "assignment_stable": self.assignment_stable,
        }


class VisitorSessionRuntime:
    """One physical visit per visible track, with conservative registered recovery.

    Anonymous tracks never inherit another track's Visit. A registered Visit may
    survive a short tracker-ID change only when the new track has independently
    confirmed the same identity_id. Every Visit expires from a monotonic watchdog
    after two seconds without a visible confirmed track.
    """

    def __init__(self, settings: VisitorSessionSettings) -> None:
        self.settings = settings
        self.sessions: dict[str, VisitorSession] = {}
        self.track_to_session: dict[int, str] = {}
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
            visible_ids = {int(track["track_id"]) for track in visible_tracks}
            for session in self.sessions.values():
                if session.active_track_id not in visible_ids:
                    session.active_track_id = None

            by_track = {int(track["track_id"]): dict(track) for track in tracks}
            assigned_sessions: set[str] = set()
            visible_tracks.sort(
                key=lambda item: (
                    not bool(item.get("primary")),
                    -float(item.get("area_ratio", 0.0)),
                )
            )

            for item in visible_tracks:
                track_id = int(item["track_id"])
                identity = identities.get(track_id)
                identity_id = None if identity is None else identity.get("identity_id")
                mapped_id = self.track_to_session.get(track_id)
                session = self.sessions.get(mapped_id or "")
                recovery_reason: str | None = None

                if session is None and identity_id:
                    session = self._registered_recovery(
                        identity_id=str(identity_id),
                        now=now,
                        assigned_sessions=assigned_sessions,
                    )
                    if session is not None:
                        recovery_reason = "independently_confirmed_registered_identity"
                        session.recovery_count += 1
                        session.last_recovery_reason = recovery_reason
                        self.recovery_count += 1
                        events.append(
                            (
                                "visitor_session_recovered",
                                {
                                    "visitor_session_id": session.visitor_session_id,
                                    "previous_track_id": session.last_track_id,
                                    "current_track_id": track_id,
                                    "reason": recovery_reason,
                                    "identity_id": identity_id,
                                },
                            )
                        )

                if session is None:
                    session = self._create_session(item, now)
                    events.append(
                        (
                            "visitor_session_started",
                            {
                                "visitor_session_id": session.visitor_session_id,
                                "visit_id": session.visitor_session_id,
                                "track_id": track_id,
                            },
                        )
                    )

                self.track_to_session[track_id] = session.visitor_session_id
                previous_identity_id = (
                    None if session.identity is None else session.identity.get("identity_id")
                )
                session.update_visible(
                    track=item,
                    face_embedding=face_embeddings.get(track_id),
                    body_embedding=body_embeddings.get(track_id),
                    identity=identity,
                    now=now,
                    settings=self.settings,
                )
                assigned_sessions.add(session.visitor_session_id)
                if session.identity is not None and session.identity.get("identity_id") != previous_identity_id:
                    events.append(
                        (
                            "visitor_session_identified",
                            {
                                "visitor_session_id": session.visitor_session_id,
                                "visit_id": session.visitor_session_id,
                                "track_id": track_id,
                                "identity_id": session.identity.get("identity_id"),
                                "display_name": session.identity.get("display_name"),
                            },
                        )
                    )
                by_track[track_id] = self._enrich_track(item, session, now)

            for track_id, item in list(by_track.items()):
                if track_id in visible_ids:
                    continue
                session = self.sessions.get(self.track_to_session.get(track_id, ""))
                if session is not None:
                    by_track[track_id] = self._enrich_track(item, session, now)

            for session in self._expire_locked(now):
                events.append(("visitor_session_expired", self._expired_payload(session)))

            return [by_track[key] for key in sorted(by_track)], events

    def expire_due(
        self,
        now: float | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            return [
                ("visitor_session_expired", self._expired_payload(session))
                for session in self._expire_locked(now)
            ]

    def _registered_recovery(
        self,
        *,
        identity_id: str,
        now: float,
        assigned_sessions: set[str],
    ) -> VisitorSession | None:
        candidates = [
            session
            for session in self.sessions.values()
            if session.active_track_id is None
            and session.visitor_session_id not in assigned_sessions
            and now - session.last_seen_monotonic <= VISIT_ABSENCE_SECONDS
            and session.identity is not None
            and session.identity.get("identity_id") == identity_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.last_seen_monotonic)

    def _enrich_track(
        self,
        track: dict[str, Any],
        session: VisitorSession,
        now: float,
    ) -> dict[str, Any]:
        item = dict(track)
        item["visitor_session_id"] = session.visitor_session_id
        item["visit_id"] = session.visitor_session_id
        item["visitor_session"] = session.public(now)
        item["visitor_session_pending"] = False
        # Never copy a session identity into an unverified track. The current
        # track must independently earn its identity from IdentityRuntime.
        item.pop("identity_source", None)
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
            assignment_stable=True,
        )
        self.sessions[session.visitor_session_id] = session
        return session

    def _expired_payload(self, session: VisitorSession) -> dict[str, Any]:
        return {
            "visitor_session_id": session.visitor_session_id,
            "visit_id": session.visitor_session_id,
            "last_track_id": session.last_track_id,
            "identity_id": None if session.identity is None else session.identity.get("identity_id"),
            "display_name": None if session.identity is None else session.identity.get("display_name"),
            "absence_seconds": VISIT_ABSENCE_SECONDS,
        }

    def _discard_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        for track_id, mapped_id in list(self.track_to_session.items()):
            if mapped_id == session_id:
                self.track_to_session.pop(track_id, None)

    def _expire_locked(self, now: float) -> list[VisitorSession]:
        expired: list[VisitorSession] = []
        for session_id, session in list(self.sessions.items()):
            if now - session.last_seen_monotonic <= VISIT_ABSENCE_SECONDS:
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
                "ttl_seconds": VISIT_ABSENCE_SECONDS,
                "visit_absence_seconds": VISIT_ABSENCE_SECONDS,
                "session_count": len(sessions),
                "active_session_count": sum(
                    1 for item in sessions if item["active_track_id"] is not None
                ),
                "recovery_count": self.recovery_count,
                "expired_count": self.expired_count,
                "pending_match_count": 0,
                "unassigned_track_count": 0,
                "anonymous_rebind_grace_seconds": 0.0,
                "registered_identity_inheritance": False,
                "registered_recovery_requires_independent_identity": True,
                "sessions": sessions,
                "privacy": {
                    "stores_images": False,
                    "persists_anonymous_embeddings": False,
                    "memory_only": True,
                },
            }
