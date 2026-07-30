from __future__ import annotations

import time
import uuid
from typing import Any

from app.config import PresenceSettings


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


class PresenceStateMachine:
    def __init__(self, settings: PresenceSettings) -> None:
        self.settings = settings
        self.state = "absent"
        self.session_id: str | None = None
        self.enter_candidate_frames = 0
        self.engage_candidate_frames = 0
        self.last_seen_monotonic: float | None = None
        self.session_started_monotonic: float | None = None
        self.last_person_count = 0

    def _engaged_detection(self, detections: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = []
        for detection in detections:
            bottom = detection.get("bottom_center") or {}
            if float(detection.get("area_ratio") or 0.0) < self.settings.engagement_min_area_ratio:
                continue
            if point_in_polygon(float(bottom.get("x") or 0.0), float(bottom.get("y") or 0.0), self.settings.engagement_zone):
                candidates.append(detection)
        return max(candidates, key=lambda item: float(item.get("area_ratio") or 0.0), default=None)

    def update(self, detections: list[dict[str, Any]], *, now_monotonic: float | None = None) -> list[tuple[str, dict[str, Any]]]:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        events: list[tuple[str, dict[str, Any]]] = []
        count = len(detections)
        visible = count > 0
        engaged_detection = self._engaged_detection(detections)
        if visible:
            self.last_seen_monotonic = now
            self.enter_candidate_frames += 1
            if self.state == "absent" and self.enter_candidate_frames >= self.settings.enter_confirm_frames:
                self.session_id = f"visit_{uuid.uuid4().hex}"
                self.session_started_monotonic = now
                self.state = "present"
                events.append(("visitor_entered", {"visit_session_id": self.session_id, "person_count": count, "detections": detections}))
            if engaged_detection is not None:
                self.engage_candidate_frames += 1
                if self.state == "present" and self.engage_candidate_frames >= self.settings.engage_confirm_frames:
                    self.state = "engaged"
                    events.append(("visitor_engaged", {"visit_session_id": self.session_id, "person_count": count, "primary_detection": engaged_detection}))
            else:
                self.engage_candidate_frames = 0
        else:
            self.enter_candidate_frames = 0
            self.engage_candidate_frames = 0
            if self.state != "absent" and self.last_seen_monotonic is not None:
                absence = now - self.last_seen_monotonic
                if absence >= self.settings.left_timeout_seconds:
                    duration = now - self.session_started_monotonic if self.session_started_monotonic is not None else None
                    events.append(("visitor_left", {"visit_session_id": self.session_id, "duration_seconds": round(duration, 3) if duration is not None else None, "absence_seconds": round(absence, 3)}))
                    self.state = "absent"
                    self.session_id = None
                    self.session_started_monotonic = None
                    self.last_seen_monotonic = None
        if count > 1 and self.last_person_count <= 1:
            events.append(("group_detected", {"person_count": count, "visit_session_id": self.session_id}))
        self.last_person_count = count
        return events

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "visit_session_id": self.session_id,
            "person_count": self.last_person_count,
            "enter_candidate_frames": self.enter_candidate_frames,
            "engage_candidate_frames": self.engage_candidate_frames,
            "last_seen_age_seconds": round(time.monotonic() - self.last_seen_monotonic, 3) if self.last_seen_monotonic is not None else None,
        }
