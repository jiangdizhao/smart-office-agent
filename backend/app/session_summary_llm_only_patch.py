from __future__ import annotations

import json
from typing import Any

from app import contact_record_api, session_summary_llm_patch

_original_upsert_summary = contact_record_api._upsert_summary
_original_save_llm_synthesis = session_summary_llm_patch._save_llm_synthesis


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except Exception:
        parsed = []
    return [str(item).strip() for item in (parsed or []) if str(item).strip()]


def _fingerprint(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _merge_append_only(existing: list[str], incoming: list[str], maximum: int = 24) -> list[str]:
    result = list(existing)
    seen = {_fingerprint(item) for item in result if _fingerprint(item)}
    for item in incoming:
        clean = str(item).strip()
        marker = _fingerprint(clean)
        if not clean or not marker or marker in seen:
            continue
        result.append(clean)
        seen.add(marker)
        if len(result) >= maximum:
            break
    return result


def _blank_non_llm_summary(req: Any) -> dict[str, Any]:
    """Create the summary shell once without erasing previously generated LLM points."""

    payload = _original_upsert_summary(req)
    summary = payload.get("summary") if isinstance(payload, dict) else None
    summary_id = str(summary.get("summary_id") or "") if isinstance(summary, dict) else ""
    if not summary_id:
        return payload

    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        row = connection.execute(
            "SELECT bullet_points_json FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
        has_existing_llm_points = bool(row and _json_list(row["bullet_points_json"]))
        if has_existing_llm_points:
            current = connection.execute(
                "SELECT * FROM session_summaries WHERE summary_id = ?",
                (summary_id,),
            ).fetchone()
            if current is not None and isinstance(payload, dict):
                payload["summary"] = contact_record_api._summary_row(current)
            return payload

        now = contact_record_api._now_iso()
        connection.execute(
            """
            UPDATE session_summaries
            SET bullet_points_json = '[]', interests_json = '[]',
                actions_json = '[]', follow_ups_json = '[]', updated_at = ?
            WHERE summary_id = ?
            """,
            (now, summary_id),
        )
        connection.execute(
            """
            UPDATE session_summary_profiles
            SET overview = '', visitor_profile_json = '{}', updated_at = ?
            WHERE summary_id = ?
            """,
            (now, summary_id),
        )
        connection.commit()
        current = connection.execute(
            "SELECT * FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
    if current is not None and isinstance(payload, dict):
        payload["summary"] = contact_record_api._summary_row(current)
    return payload


def _llm_only_instructions(language: str) -> str:
    language_rule = (
        "Write every bullet in concise natural English."
        if language == "en"
        else "所有要点必须使用简洁、自然的中文。"
    )
    return f"""
You create an LLM-only key-point summary of one Smart Office visitor session.
{language_rule}
Return exactly one valid JSON object with the established summary schema.
This is a summary, never a transcript. Do not reproduce complete utterances, dialogue order, quotations, timestamps, greetings, filler, repeated text, or Sara's sales boilerplate.
Every array item must be a compact semantic key point, normally no more than 25 Chinese characters or 18 English words.
Merge repeated facts. Keep only information useful for understanding the visitor, requested functions, verified outcomes, unresolved needs and follow-up.
Only include personal attributes explicitly stated by the visitor. Never infer age, income, budget, authority, personality, emotion, identity or purchasing power.
Treat an Office action as completed only when an assistant message explicitly reports completion or verification.
Use null or an empty list when evidence is absent. Commercial-intent values must be exactly interested, not_interested or unknown.
Return JSON only, without Markdown fences or commentary.
""".strip()


def _compact_bullets(synthesis: dict[str, Any], language: str) -> list[str]:
    bullets: list[str] = []

    def add(prefix: str, values: list[str], maximum: int) -> None:
        clean = [str(value).strip() for value in values if str(value).strip()]
        if clean:
            bullets.append(f"{prefix}：{'；'.join(clean[:maximum])}。")

    overview = str(synthesis.get("overview") or "").strip()
    if overview:
        bullets.append(overview)
    profile = synthesis.get("visitor_profile") or {}
    profile_items: list[str] = []
    if profile.get("confirmed_role"):
        profile_items.append(f"角色 {profile['confirmed_role']}")
    if profile.get("confirmed_industry"):
        profile_items.append(f"行业 {profile['confirmed_industry']}")
    profile_items.extend(list(profile.get("responsibilities") or [])[:2])
    add("Visitor profile" if language == "en" else "用户特征", profile_items, 4)
    add("Core needs" if language == "en" else "核心需求", [*list(profile.get("goals") or [])[:2], *list(profile.get("pain_points") or [])[:2]], 4)
    add("Main topics" if language == "en" else "主要主题", list(synthesis.get("conversation_topics") or []), 5)
    add("Requested actions" if language == "en" else "请求事项", list(synthesis.get("requested_actions") or []), 4)
    add("Completed actions" if language == "en" else "已完成操作", list(synthesis.get("completed_actions") or []), 4)
    add("Unresolved items" if language == "en" else "未解决事项", list(synthesis.get("unresolved_items") or []), 4)
    add("Recommended follow-up" if language == "en" else "建议后续", list(synthesis.get("recommended_follow_up") or []), 4)
    return bullets[:8]


def _save_llm_synthesis_append_only(*, summary_id: str, synthesis: dict[str, Any], language: str) -> dict[str, Any]:
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        row = connection.execute(
            "SELECT bullet_points_json FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
        existing = _json_list(row["bullet_points_json"]) if row else []

    saved = _original_save_llm_synthesis(
        summary_id=summary_id,
        synthesis=synthesis,
        language=language,
    )
    incoming = [str(item).strip() for item in saved.get("bullet_points", []) if str(item).strip()]
    merged = _merge_append_only(existing, incoming)
    now = contact_record_api._now_iso()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        connection.execute(
            "UPDATE session_summaries SET bullet_points_json = ?, updated_at = ? WHERE summary_id = ?",
            (json.dumps(merged, ensure_ascii=False), now, summary_id),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("The append-only LLM summary could not be read back.")
    return contact_record_api._summary_row(row)


contact_record_api._upsert_summary = _blank_non_llm_summary
session_summary_llm_patch._summary_instructions = _llm_only_instructions
session_summary_llm_patch._bullet_points = _compact_bullets
session_summary_llm_patch._save_llm_synthesis = _save_llm_synthesis_append_only
