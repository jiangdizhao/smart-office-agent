from __future__ import annotations

import sqlite3

from app import contact_record_api


def _safe_bind_context_records(
    connection: sqlite3.Connection,
    contact_id: str,
    conversation_id: str,
    visit_id: str | None,
) -> None:
    """Bind summaries/bookings without crossing Visit boundaries.

    The browser intentionally reuses one conversation_id across exhibition visitors.
    When a real visit_id is available it is the sole identity boundary. Conversation
    fallback is permitted only for legacy records that have no Visit identifier.
    """

    updated_at = contact_record_api._now_iso()
    clean_visit = str(visit_id or "").strip()
    if clean_visit:
        connection.execute(
            """
            UPDATE session_summaries
            SET contact_id = ?, updated_at = ?
            WHERE contact_id IS NULL AND visit_id = ?
            """,
            (contact_id, updated_at, clean_visit),
        )
        connection.execute(
            """
            UPDATE meeting_bookings
            SET contact_id = ?, updated_at = ?
            WHERE contact_id IS NULL AND visit_id = ?
            """,
            (contact_id, updated_at, clean_visit),
        )
        return

    connection.execute(
        """
        UPDATE session_summaries
        SET contact_id = ?, updated_at = ?
        WHERE contact_id IS NULL
          AND conversation_id = ?
          AND (visit_id IS NULL OR TRIM(visit_id) = '')
        """,
        (contact_id, updated_at, conversation_id),
    )
    connection.execute(
        """
        UPDATE meeting_bookings
        SET contact_id = ?, updated_at = ?
        WHERE contact_id IS NULL
          AND conversation_id = ?
          AND (visit_id IS NULL OR TRIM(visit_id) = '')
        """,
        (contact_id, updated_at, conversation_id),
    )


contact_record_api._bind_context_records = _safe_bind_context_records
