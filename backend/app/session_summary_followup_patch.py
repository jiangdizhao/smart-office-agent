from __future__ import annotations

from typing import Any

from app import contact_record_api

_original_summarise_messages = contact_record_api._summarise_messages


def _summary_with_follow_up(messages: list[dict[str, Any]]) -> dict[str, list[str]]:
    result = _original_summarise_messages(messages)
    bullets = list(result.get("bullet_points") or [])
    follow_ups = list(result.get("follow_ups") or [])
    if follow_ups and not any("建议后续" in item or "后续建议" in item for item in bullets):
        bullets.append(f"建议后续：{'；'.join(follow_ups[:3])}")
    result["bullet_points"] = bullets[:8]
    return result


contact_record_api._summarise_messages = _summary_with_follow_up
