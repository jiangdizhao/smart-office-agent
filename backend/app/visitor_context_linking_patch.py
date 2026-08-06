from __future__ import annotations

import sqlite3
from datetime import datetime

from app import contact_record_api


_original_contact_dict = contact_record_api._contact_dict


def _safe_bind_context_records(
    connection: sqlite3.Connection,
    contact_id: str,
    conversation_id: str,
    visit_id: str | None,
) -> None:
    """Bind summaries/bookings without crossing Visit boundaries."""

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


def _profile_contact_dict(row: sqlite3.Row) -> dict:
    """Return identical appointment/summary metadata in list and detail views."""

    result = _original_contact_dict(row)
    keys = set(row.keys())
    if {
        "appointment_count",
        "session_summary_count",
        "next_appointment_at",
    }.issubset(keys):
        next_at = row["next_appointment_at"]
        result.update(
            {
                "appointment_count": int(row["appointment_count"] or 0),
                "session_summary_count": int(row["session_summary_count"] or 0),
                "next_appointment_at": next_at,
                "has_upcoming_appointment": bool(next_at),
                "latest_activity_at": (
                    row["latest_activity_at"]
                    if "latest_activity_at" in keys and row["latest_activity_at"]
                    else row["updated_at"]
                ),
            }
        )
        return result

    contact_id = str(row["contact_id"])
    now = datetime.now(contact_record_api._timezone()).isoformat()
    with contact_record_api._connect() as connection:
        appointment = connection.execute(
            """
            SELECT COUNT(*) AS appointment_count,
                   MIN(CASE WHEN status = 'confirmed' AND start_at >= ? THEN start_at END)
                       AS next_appointment_at
            FROM meeting_bookings
            WHERE contact_id = ?
            """,
            (now, contact_id),
        ).fetchone()
        summary = connection.execute(
            "SELECT COUNT(*) AS summary_count FROM session_summaries WHERE contact_id = ?",
            (contact_id,),
        ).fetchone()
    next_at = appointment["next_appointment_at"] if appointment else None
    result.update(
        {
            "appointment_count": int(appointment["appointment_count"] or 0) if appointment else 0,
            "session_summary_count": int(summary["summary_count"] or 0) if summary else 0,
            "next_appointment_at": next_at,
            "has_upcoming_appointment": bool(next_at),
            "latest_activity_at": row["updated_at"],
        }
    )
    return result


contact_record_api._bind_context_records = _safe_bind_context_records
contact_record_api._contact_dict = _profile_contact_dict

# Patches are installed after the base contact router has defined its endpoint
# functions. The endpoint resolves these module globals at request time, so the
# enhanced implementation merges browser events with the authoritative backend
# conversation store and summarizes casual chat as well as Office activity.
from app import session_summary_patch as _session_summary_patch  # noqa: E402,F401

# Install the 09:00-18:00 hourly timeline after the base contact/booking module is
# loaded. This replaces the built-in fake employee fallback with the real staff
# catalog from config/demo_meeting_staff.json.
from app import meeting_hourly_timeline_patch as _meeting_hourly_timeline_patch  # noqa: E402,F401
