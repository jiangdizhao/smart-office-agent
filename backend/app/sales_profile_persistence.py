from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from app.sales_models import SalesSessionState, utc_now_iso

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCK = RLock()


def _database_path() -> Path:
    configured = os.getenv("SMART_OFFICE_CONTACT_DB", "data/contact_records.sqlite3").strip()
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    path = candidate.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path(), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _initialise() -> None:
    with _LOCK, _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_profile_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                visit_id TEXT NOT NULL,
                language TEXT NOT NULL,
                sales_stage TEXT NOT NULL,
                explicit_facts_json TEXT NOT NULL,
                pain_points_json TEXT NOT NULL,
                interested_capabilities_json TEXT NOT NULL,
                objections_json TEXT NOT NULL,
                demonstrated_capabilities_json TEXT NOT NULL,
                booking_offer_count INTEGER NOT NULL DEFAULT 0,
                contact_offer_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(contact_id, visit_id),
                FOREIGN KEY(contact_id) REFERENCES contact_records(contact_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sales_profile_snapshots_visit_id
            ON sales_profile_snapshots(visit_id)
            """
        )
        connection.commit()


def _consented_contact(connection: sqlite3.Connection, state: SalesSessionState) -> str | None:
    row = connection.execute(
        """
        SELECT contact_id FROM contact_records
        WHERE contact_consent = 1 AND visit_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (state.visit_id,),
    ).fetchone()
    if row:
        return str(row["contact_id"])
    row = connection.execute(
        """
        SELECT contact_id FROM contact_records
        WHERE contact_consent = 1 AND conversation_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (state.conversation_id,),
    ).fetchone()
    return str(row["contact_id"]) if row else None


class SalesProfilePersistence:
    """Persist an explicit sales summary only after contact consent exists.

    Raw transcripts, audio, inferred demographics, biometric data and face features
    are intentionally absent from this schema.
    """

    def __init__(self) -> None:
        self._ready = False

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        _initialise()
        self._ready = True

    def persist_if_consented(self, state: SalesSessionState) -> bool:
        self._ensure_ready()
        now = utc_now_iso()
        with _LOCK, _connect() as connection:
            contact_id = _consented_contact(connection, state)
            if not contact_id:
                return False
            snapshot_id = f"sales-{contact_id}-{state.visit_id}"
            connection.execute(
                """
                INSERT INTO sales_profile_snapshots (
                    snapshot_id, contact_id, conversation_id, visit_id, language,
                    sales_stage, explicit_facts_json, pain_points_json,
                    interested_capabilities_json, objections_json,
                    demonstrated_capabilities_json, booking_offer_count,
                    contact_offer_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contact_id, visit_id) DO UPDATE SET
                    language = excluded.language,
                    sales_stage = excluded.sales_stage,
                    explicit_facts_json = excluded.explicit_facts_json,
                    pain_points_json = excluded.pain_points_json,
                    interested_capabilities_json = excluded.interested_capabilities_json,
                    objections_json = excluded.objections_json,
                    demonstrated_capabilities_json = excluded.demonstrated_capabilities_json,
                    booking_offer_count = excluded.booking_offer_count,
                    contact_offer_count = excluded.contact_offer_count,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot_id,
                    contact_id,
                    state.conversation_id,
                    state.visit_id,
                    state.language,
                    state.stage,
                    json.dumps(state.explicit_facts, ensure_ascii=False, sort_keys=True),
                    json.dumps(state.pain_points, ensure_ascii=False),
                    json.dumps(state.interested_capabilities, ensure_ascii=False),
                    json.dumps(state.objections, ensure_ascii=False),
                    json.dumps(state.demonstrated_capabilities, ensure_ascii=False),
                    state.booking_offer_count,
                    state.contact_offer_count,
                    now,
                    now,
                ),
            )
            connection.commit()
            return True

    def status(self) -> dict[str, Any]:
        try:
            self._ensure_ready()
            with _LOCK, _connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM sales_profile_snapshots"
                ).fetchone()
            return {
                "ok": True,
                "schema": "sales-profile-snapshot-v1",
                "snapshot_count": int(row["count"] if row else 0),
                "requires_contact_consent": True,
                "stores_raw_transcript": False,
                "stores_audio": False,
                "stores_biometrics": False,
            }
        except Exception as exc:
            return {
                "ok": False,
                "schema": "sales-profile-snapshot-v1",
                "error": f"{type(exc).__name__}: {exc}",
                "requires_contact_consent": True,
                "stores_raw_transcript": False,
                "stores_audio": False,
                "stores_biometrics": False,
            }


sales_profile_persistence = SalesProfilePersistence()
