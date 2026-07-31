from __future__ import annotations

from typing import Any

import numpy as np

import app.visitor_session as visitor_session_module
from app.visitor_session import VisitorSession


class IsolatedVisitorSessionRuntime(visitor_session_module.VisitorSessionRuntime):
    """Visit runtime that never lends a registered identity to an unverified track."""

    def _all_inactive_candidates(
        self,
        *,
        now: float,
        assigned_sessions: set[str],
        excluded_session_ids: set[str],
    ) -> list[VisitorSession]:
        return [
            session
            for session in self.sessions.values()
            if now - session.last_seen_monotonic <= self.settings.ttl_seconds
            and session.visitor_session_id not in assigned_sessions
            and session.visitor_session_id not in excluded_session_ids
            and session.active_track_id is None
        ]

    def _inactive_candidates(
        self,
        *,
        now: float,
        assigned_sessions: set[str],
        excluded_session_ids: set[str],
    ) -> list[VisitorSession]:
        # Face/body/spatial recovery is permitted only for anonymous visits. A
        # registered visit may be recovered only after the new track has
        # independently confirmed the same identity_id from the consented gallery.
        return [
            session
            for session in self._all_inactive_candidates(
                now=now,
                assigned_sessions=assigned_sessions,
                excluded_session_ids=excluded_session_ids,
            )
            if session.identity is None
        ]

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
        identity_id = None if identity is None else identity.get("identity_id")
        if identity_id:
            identity_matches = [
                session
                for session in self._all_inactive_candidates(
                    now=now,
                    assigned_sessions=assigned_sessions,
                    excluded_session_ids=excluded_session_ids,
                )
                if session.identity is not None
                and session.identity.get("identity_id") == identity_id
            ]
            if identity_matches:
                selected = max(
                    identity_matches,
                    key=lambda item: item.last_seen_monotonic,
                )
                self._pending_matches.pop(track_id, None)
                return selected, "independently_confirmed_registered_identity", {
                    "identity_id": identity_id,
                }

            # This is a newly confirmed registered visitor, not an anonymous
            # continuation. Start a fresh physical Visit.
            self._pending_matches.pop(track_id, None)
            return None, None, {"identity_id": identity_id, "new_registered_visit": True}

        return super()._match_session(
            track=track,
            face_embedding=face_embedding,
            body_embedding=body_embedding,
            identity=None,
            now=now,
            assigned_sessions=assigned_sessions,
            excluded_session_ids=excluded_session_ids,
        )

    def _enrich_track(
        self,
        track: dict[str, Any],
        session: VisitorSession,
        now: float,
    ) -> dict[str, Any]:
        item = dict(track)
        item["visitor_session_id"] = session.visitor_session_id
        item["visitor_session"] = session.public(now)
        item["visitor_session_pending"] = False
        # Deliberately do not copy session.identity into a track. The current
        # track must earn identity through IdentityRuntime/SFace confirmation.
        item.pop("identity_source", None)
        return item


def install_visit_session_isolation() -> None:
    visitor_session_module.VisitorSessionRuntime = IsolatedVisitorSessionRuntime
