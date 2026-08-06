from __future__ import annotations

import json
import os
from typing import Any, Literal

from app import contact_record_api, session_summary_patch
from app.openai_services import generate_response_text, parse_json_object

Language = Literal["zh", "en"]
_PROMPT_VERSION = "session-summary-llm-v1"
_original_summary_row = contact_record_api._summary_row


def _clean(value: Any, maximum: int = 500) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


def _string_list(value: Any, maximum_items: int, maximum_chars: int = 220) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        clean = _clean(item, maximum_chars)
        if not clean:
            continue
        fingerprint = "".join(character.casefold() for character in clean if character.isalnum())
        if not fingerprint:
            continue
        if any(
            fingerprint == "".join(character.casefold() for character in existing if character.isalnum())
            for existing in result
        ):
            continue
        result.append(clean)
        if len(result) >= maximum_items:
            break
    return result


def _profile(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "confirmed_role": _clean(source.get("confirmed_role"), 120) or None,
        "confirmed_industry": _clean(source.get("confirmed_industry"), 120) or None,
        "responsibilities": _string_list(source.get("responsibilities"), 5),
        "goals": _string_list(source.get("goals"), 5),
        "pain_points": _string_list(source.get("pain_points"), 5),
        "preferences": _string_list(source.get("preferences"), 5),
        "concerns": _string_list(source.get("concerns"), 5),
    }


def _intent_value(value: Any) -> str:
    clean = _clean(value, 40).casefold().replace("-", "_").replace(" ", "_")
    return clean if clean in {"interested", "not_interested", "unknown"} else "unknown"


def _commercial_intent(value: Any) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    return {
        "demo_interest": _intent_value(source.get("demo_interest")),
        "booking_interest": _intent_value(source.get("booking_interest")),
        "contact_interest": _intent_value(source.get("contact_interest")),
    }


def _normalise_synthesis(value: dict[str, Any]) -> dict[str, Any]:
    overview = _clean(value.get("overview"), 1_600)
    if not overview:
        raise ValueError("The language model summary did not include a coherent overview.")
    return {
        "overview": overview,
        "visitor_profile": _profile(value.get("visitor_profile")),
        "conversation_topics": _string_list(value.get("conversation_topics"), 10),
        "requested_actions": _string_list(value.get("requested_actions"), 8),
        "completed_actions": _string_list(value.get("completed_actions"), 8),
        "unresolved_items": _string_list(value.get("unresolved_items"), 8),
        "commercial_intent": _commercial_intent(value.get("commercial_intent")),
        "recommended_follow_up": _string_list(value.get("recommended_follow_up"), 8),
    }


def _summary_instructions(language: Language) -> str:
    language_rule = (
        "Write all human-readable JSON string values in English."
        if language == "en"
        else "所有供人阅读的 JSON 字符串值必须使用自然中文。"
    )
    return f"""
You create a structured summary of one Smart Office visitor session.
{language_rule}
Return exactly one valid JSON object with this schema:
{{
  "overview": "one coherent synthesis, not copied dialogue",
  "visitor_profile": {{
    "confirmed_role": null,
    "confirmed_industry": null,
    "responsibilities": [],
    "goals": [],
    "pain_points": [],
    "preferences": [],
    "concerns": []
  }},
  "conversation_topics": [],
  "requested_actions": [],
  "completed_actions": [],
  "unresolved_items": [],
  "commercial_intent": {{
    "demo_interest": "unknown",
    "booking_interest": "unknown",
    "contact_interest": "unknown"
  }},
  "recommended_follow_up": []
}}
Synthesize meaning across turns. Do not reproduce the conversation line by line and do not prefix items with phrases such as “the visitor said” unless attribution is genuinely necessary.
Only include role, industry, responsibilities, goals, pain points, preferences and concerns that the visitor explicitly stated. Never infer age, income, budget, authority, personality, emotion, identity or purchasing power.
Treat an Office action as completed only when an assistant message explicitly reports that it completed or was verified. A request alone is not a completion.
Remove greetings, repeated transcriptions, filler, Sara's sales boilerplate and duplicate facts.
Use null or an empty list when evidence is absent. Each commercial-intent value must be exactly interested, not_interested or unknown.
The overview should normally be one concise paragraph that explains the visitor's context, main need, important topics, outcome and any unresolved follow-up.
Return JSON only, without Markdown fences or commentary.
""".strip()


def _message_payload(messages: list[tuple[str, str, str]]) -> str:
    return json.dumps(
        {
            "conversation": [
                {
                    "role": role,
                    "text": text,
                    "timestamp": timestamp or None,
                }
                for role, text, timestamp in messages
            ]
        },
        ensure_ascii=False,
        indent=2,
    )


def _profile_sentence(profile: dict[str, Any], language: Language) -> str:
    values: list[str] = []
    role = profile.get("confirmed_role")
    industry = profile.get("confirmed_industry")
    if role:
        values.append(("Role" if language == "en" else "职业/角色") + f"：{role}")
    if industry:
        values.append(("Industry" if language == "en" else "行业") + f"：{industry}")
    responsibilities = profile.get("responsibilities") or []
    if responsibilities:
        values.append(
            ("Responsibilities" if language == "en" else "职责")
            + f"：{'；'.join(responsibilities[:3])}"
        )
    if not values:
        return ""
    prefix = "Visitor profile" if language == "en" else "用户特征"
    return f"{prefix}：{'；'.join(values)}。"


def _needs_sentence(profile: dict[str, Any], language: Language) -> str:
    values: list[str] = []
    goals = profile.get("goals") or []
    pain_points = profile.get("pain_points") or []
    if goals:
        values.append(("Goals" if language == "en" else "目标") + f"：{'；'.join(goals[:3])}")
    if pain_points:
        values.append(("Pain points" if language == "en" else "痛点") + f"：{'；'.join(pain_points[:3])}")
    if not values:
        return ""
    prefix = "Core needs" if language == "en" else "核心需求"
    return f"{prefix}：{'；'.join(values)}。"


def _commercial_sentence(intent: dict[str, str], language: Language) -> str:
    if all(value == "unknown" for value in intent.values()):
        return ""
    if language == "en":
        return (
            "Commercial intent: "
            f"demo={intent['demo_interest']}; booking={intent['booking_interest']}; "
            f"contact={intent['contact_interest']}."
        )
    labels = {"interested": "有意愿", "not_interested": "无意愿", "unknown": "未明确"}
    return (
        "商业意向："
        f"体验演示={labels[intent['demo_interest']]}；"
        f"预约={labels[intent['booking_interest']]}；"
        f"后续联系={labels[intent['contact_interest']]}。"
    )


def _bullet_points(synthesis: dict[str, Any], language: Language) -> list[str]:
    bullets = [synthesis["overview"]]
    profile = synthesis["visitor_profile"]
    for value in (
        _profile_sentence(profile, language),
        _needs_sentence(profile, language),
    ):
        if value:
            bullets.append(value)
    topics = synthesis["conversation_topics"]
    if topics:
        bullets.append(
            ("Main topics" if language == "en" else "主要主题")
            + f"：{'；'.join(topics[:5])}。"
        )
    completed = synthesis["completed_actions"]
    if completed:
        bullets.append(
            ("Verified outcomes" if language == "en" else "已完成操作")
            + f"：{'；'.join(completed[:4])}。"
        )
    unresolved = synthesis["unresolved_items"]
    if unresolved:
        bullets.append(
            ("Unresolved items" if language == "en" else "未解决事项")
            + f"：{'；'.join(unresolved[:4])}。"
        )
    commercial = _commercial_sentence(synthesis["commercial_intent"], language)
    if commercial:
        bullets.append(commercial)
    follow_up = synthesis["recommended_follow_up"]
    if follow_up:
        bullets.append(
            ("Recommended follow-up" if language == "en" else "建议后续")
            + f"：{'；'.join(follow_up[:4])}。"
        )
    return bullets[:8]


def _initialise_generation_table() -> None:
    session_summary_patch._initialise_profile_table()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_summary_generation (
                summary_id TEXT PRIMARY KEY,
                summary_mode TEXT NOT NULL,
                model TEXT,
                error TEXT,
                source_message_count INTEGER NOT NULL DEFAULT 0,
                prompt_version TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                FOREIGN KEY(summary_id) REFERENCES session_summaries(summary_id) ON DELETE CASCADE
            )
            """
        )
        connection.commit()


def _save_generation_metadata(
    *,
    summary_id: str,
    summary_mode: str,
    model: str | None,
    error: str | None,
    source_message_count: int,
) -> None:
    _initialise_generation_table()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        connection.execute(
            """
            INSERT INTO session_summary_generation (
                summary_id, summary_mode, model, error, source_message_count,
                prompt_version, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(summary_id) DO UPDATE SET
                summary_mode = excluded.summary_mode,
                model = excluded.model,
                error = excluded.error,
                source_message_count = excluded.source_message_count,
                prompt_version = excluded.prompt_version,
                generated_at = excluded.generated_at
            """,
            (
                summary_id,
                summary_mode,
                model,
                _clean(error, 2_000) or None,
                source_message_count,
                _PROMPT_VERSION,
                contact_record_api._now_iso(),
            ),
        )
        connection.commit()


def _load_generation_metadata(summary_id: str, status: str) -> dict[str, Any]:
    _initialise_generation_table()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        row = connection.execute(
            """
            SELECT summary_mode, model, error, source_message_count,
                   prompt_version, generated_at
            FROM session_summary_generation WHERE summary_id = ?
            """,
            (summary_id,),
        ).fetchone()
    if row is None:
        return {
            "summary_mode": "deterministic_draft" if status == "draft" else "deterministic_legacy",
            "summary_model": None,
            "summary_error": None,
            "summary_source_message_count": 0,
            "summary_prompt_version": None,
            "summary_generated_at": None,
        }
    return {
        "summary_mode": str(row["summary_mode"] or "deterministic_fallback"),
        "summary_model": str(row["model"] or "") or None,
        "summary_error": str(row["error"] or "") or None,
        "summary_source_message_count": int(row["source_message_count"] or 0),
        "summary_prompt_version": str(row["prompt_version"] or "") or None,
        "summary_generated_at": str(row["generated_at"] or "") or None,
    }


def _summary_row_with_generation(row: Any) -> dict[str, Any]:
    result = _original_summary_row(row)
    result.update(
        _load_generation_metadata(
            str(result.get("summary_id") or ""),
            str(result.get("status") or "draft"),
        )
    )
    return result


def _save_llm_synthesis(
    *,
    summary_id: str,
    synthesis: dict[str, Any],
    language: Language,
) -> dict[str, Any]:
    bullets = _bullet_points(synthesis, language)
    interests = synthesis["conversation_topics"]
    actions = [
        *synthesis["requested_actions"],
        *[
            ("Completed: " if language == "en" else "已完成：") + value
            for value in synthesis["completed_actions"]
        ],
    ]
    follow_ups = synthesis["recommended_follow_up"]
    now = contact_record_api._now_iso()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        connection.execute(
            """
            UPDATE session_summaries
            SET status = 'final',
                bullet_points_json = ?,
                interests_json = ?,
                actions_json = ?,
                follow_ups_json = ?,
                updated_at = ?,
                finalized_at = ?
            WHERE summary_id = ?
            """,
            (
                json.dumps(bullets, ensure_ascii=False),
                json.dumps(interests, ensure_ascii=False),
                json.dumps(actions[:12], ensure_ascii=False),
                json.dumps(follow_ups, ensure_ascii=False),
                now,
                now,
                summary_id,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
    session_summary_patch._save_extended_profile(
        summary_id,
        {
            "_overview": [synthesis["overview"]],
            "_visitor_profile": [json.dumps(synthesis["visitor_profile"], ensure_ascii=False)],
        },
    )
    if row is None:
        raise RuntimeError("The saved session summary could not be read back.")
    return contact_record_api._summary_row(row)


def _summary_row_by_id(summary_id: str) -> dict[str, Any]:
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        row = connection.execute(
            "SELECT * FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("The deterministic session summary was not saved.")
    return contact_record_api._summary_row(row)


contact_record_api._summary_row = _summary_row_with_generation


@contact_record_api.router.put("/api/visitor-experience/session-summaries/llm")
async def upsert_llm_session_summary(
    req: contact_record_api.SessionSummaryUpsertRequest,
) -> dict[str, Any]:
    final_req = req.model_copy(update={"status": "final"})
    deterministic = contact_record_api._upsert_summary(final_req)
    base_summary = deterministic.get("summary") if isinstance(deterministic, dict) else None
    if not isinstance(base_summary, dict):
        raise RuntimeError("The deterministic summary prerequisite did not return a summary.")
    summary_id = str(base_summary.get("summary_id") or "")
    merged = session_summary_patch._merged_messages(final_req)
    useful = session_summary_patch._substantive_messages(
        [item.model_dump() for item in merged]
    )

    if not useful:
        _save_generation_metadata(
            summary_id=summary_id,
            summary_mode="deterministic_fallback",
            model=None,
            error="No substantive visitor messages were available for LLM synthesis.",
            source_message_count=0,
        )
        return {
            "ok": True,
            "summary": _summary_row_by_id(summary_id),
            "summary_mode": "deterministic_fallback",
        }

    selected_model = (
        os.getenv("SMART_OFFICE_SESSION_SUMMARY_MODEL", "").strip()
        or None
    )
    try:
        answer, model = await generate_response_text(
            input_text=_message_payload(useful),
            instructions=_summary_instructions(final_req.language),
            model=selected_model,
            max_output_tokens=1_600,
        )
        synthesis = _normalise_synthesis(parse_json_object(answer))
        summary = _save_llm_synthesis(
            summary_id=summary_id,
            synthesis=synthesis,
            language=final_req.language,
        )
        _save_generation_metadata(
            summary_id=summary_id,
            summary_mode="llm",
            model=model,
            error=None,
            source_message_count=len(useful),
        )
        summary.update(_load_generation_metadata(summary_id, "final"))
        return {
            "ok": True,
            "summary": summary,
            "summary_mode": "llm",
        }
    except Exception as exc:
        _save_generation_metadata(
            summary_id=summary_id,
            summary_mode="deterministic_fallback",
            model=selected_model,
            error=str(exc),
            source_message_count=len(useful),
        )
        return {
            "ok": True,
            "summary": _summary_row_by_id(summary_id),
            "summary_mode": "deterministic_fallback",
            "warning": _clean(exc, 800),
        }
