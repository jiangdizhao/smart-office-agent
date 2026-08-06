from __future__ import annotations

import os
from typing import Any

from app import contact_record_api, session_summary_llm_patch, session_summary_patch
from app.openai_services import generate_response_text, parse_json_object


def _current_revision(summary_id: str) -> int:
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        row = connection.execute(
            "SELECT source_revision FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
    return int(row["source_revision"] or 0) if row else -1


def _restore_draft(summary_id: str, source_revision: int) -> dict[str, Any]:
    now = contact_record_api._now_iso()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        connection.execute(
            """
            UPDATE session_summaries
            SET status = 'draft', finalized_at = NULL, updated_at = ?
            WHERE summary_id = ? AND source_revision = ?
            """,
            (now, summary_id, source_revision),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("The draft session summary could not be read back.")
    return contact_record_api._summary_row(row)


@contact_record_api.router.put("/api/visitor-experience/session-summaries/llm-draft")
async def upsert_llm_draft_session_summary(
    req: contact_record_api.SessionSummaryUpsertRequest,
) -> dict[str, Any]:
    draft_req = req.model_copy(update={"status": "draft"})
    deterministic = contact_record_api._upsert_summary(draft_req)
    base_summary = deterministic.get("summary") if isinstance(deterministic, dict) else None
    if not isinstance(base_summary, dict):
        raise RuntimeError("The deterministic draft prerequisite did not return a summary.")
    summary_id = str(base_summary.get("summary_id") or "")
    merged = session_summary_patch._merged_messages(draft_req)
    useful = session_summary_patch._substantive_messages(
        [item.model_dump() for item in merged]
    )

    if not useful:
        session_summary_llm_patch._save_generation_metadata(
            summary_id=summary_id,
            summary_mode="deterministic_draft",
            model=None,
            error="No substantive visitor messages were available for LLM synthesis.",
            source_message_count=0,
        )
        return {
            "ok": True,
            "summary": session_summary_llm_patch._summary_row_by_id(summary_id),
            "summary_mode": "deterministic_draft",
        }

    requested_revision = int(draft_req.source_revision)
    selected_model = (
        os.getenv("SMART_OFFICE_SESSION_SUMMARY_MODEL", "").strip()
        or None
    )
    try:
        answer, model = await generate_response_text(
            input_text=session_summary_llm_patch._message_payload(useful),
            instructions=session_summary_llm_patch._summary_instructions(draft_req.language),
            model=selected_model,
            max_output_tokens=1_600,
        )
        synthesis = session_summary_llm_patch._normalise_synthesis(
            parse_json_object(answer)
        )
        if _current_revision(summary_id) != requested_revision:
            return {
                "ok": True,
                "summary": session_summary_llm_patch._summary_row_by_id(summary_id),
                "summary_mode": "stale_llm_draft_skipped",
            }
        session_summary_llm_patch._save_llm_synthesis(
            summary_id=summary_id,
            synthesis=synthesis,
            language=draft_req.language,
        )
        summary = _restore_draft(summary_id, requested_revision)
        session_summary_llm_patch._save_generation_metadata(
            summary_id=summary_id,
            summary_mode="llm_draft",
            model=model,
            error=None,
            source_message_count=len(useful),
        )
        summary.update(
            session_summary_llm_patch._load_generation_metadata(summary_id, "draft")
        )
        return {
            "ok": True,
            "summary": summary,
            "summary_mode": "llm_draft",
        }
    except Exception as exc:
        session_summary_llm_patch._save_generation_metadata(
            summary_id=summary_id,
            summary_mode="deterministic_draft",
            model=selected_model,
            error=str(exc),
            source_message_count=len(useful),
        )
        return {
            "ok": True,
            "summary": session_summary_llm_patch._summary_row_by_id(summary_id),
            "summary_mode": "deterministic_draft",
            "warning": session_summary_llm_patch._clean(exc, 800),
        }
