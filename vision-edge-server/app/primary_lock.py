from __future__ import annotations

from typing import Any

from app.config import PresenceSettings, PrimarySettings
from app.tracking_runtime import PrimarySelector, Track


class OcclusionAwarePrimarySelector(PrimarySelector):
    """Retain the selected primary while its confirmed track is temporarily absent.

    MultiObjectTracker supplies currently confirmed tracks to the selector. This wrapper stores
    the last time the primary was visible, so a short transition to the track's ``lost`` state
    does not allow a nearby challenger to steal the conversation immediately.
    """

    def __init__(self, settings: PrimarySettings, presence: PresenceSettings) -> None:
        super().__init__(settings, presence)
        self._primary_last_visible_at: float | None = None

    def update(
        self,
        tracks: list[Track],
        now: float,
    ) -> tuple[int | None, dict[str, Any] | None]:
        current = next(
            (track for track in tracks if track.track_id == self.primary_track_id and track.visible),
            None,
        )
        if current is not None:
            self._primary_last_visible_at = now
        elif (
            self.primary_track_id is not None
            and self._primary_last_visible_at is not None
            and now - self._primary_last_visible_at <= self.settings.lost_lock_seconds
        ):
            self.scores = {track.track_id: self.score(track, now) for track in tracks}
            return self.primary_track_id, None

        primary_id, event = super().update(tracks, now)
        if primary_id is None:
            self._primary_last_visible_at = None
        elif any(track.track_id == primary_id and track.visible for track in tracks):
            self._primary_last_visible_at = now
        return primary_id, event
