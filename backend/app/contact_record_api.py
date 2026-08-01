from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

router = APIRouter(tags=["contact-records"])

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_PATTERN = re.compile(r"^[0-9+()\-\s.]{5,80}$")
_LOCK = RLock()


def _database_path() -> Path:
    configured = os.getenv("SMART_OFFICE_CONTACT_DB", "data/contact_records.sqlite3").strip()
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = _REPO_ROOT / candidate
    path = candidate.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path(), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _initialise() -> None:
    with _LOCK, _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS contact_records (
                contact_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                visit_id TEXT,
                name TEXT NOT NULL,
                company TEXT,
                email TEXT,
                phone TEXT,
                interest_tags_json TEXT NOT NULL,
                notes TEXT,
                contact_consent INTEGER NOT NULL CHECK (contact_consent IN (0, 1)),
                consent_statement_version TEXT NOT NULL,
                consent_confirmed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_contact_records_created_at
                ON contact_records(created_at);
            CREATE INDEX IF NOT EXISTS idx_contact_records_visit_id
                ON contact_records(visit_id);

            CREATE TABLE IF NOT EXISTS contact_audit_events (
                audit_event_id TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL,
                action TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                source TEXT NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY(contact_id) REFERENCES contact_records(contact_id) ON DELETE CASCADE
            );
            """
        )


class ContactRecordCreateRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str | None = Field(default=None, max_length=160)
    name: str = Field(..., min_length=1, max_length=120)
    company: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=240)
    phone: str | None = Field(default=None, max_length=80)
    interest_tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=2_000)
    contact_consent: bool
    consent_statement_version: str = Field(..., min_length=1, max_length=80)
    source: str = Field("left_touch_display", min_length=1, max_length=80)

    @field_validator("conversation_id", "visit_id", "name", "company", "email", "phone", "notes", "source", mode="before")
    @classmethod
    def strip_optional_text(cls, value: Any) -> Any:
        if value is None:
            return None
        clean = " ".join(str(value).strip().split())
        return clean or None

    @field_validator("interest_tags")
    @classmethod
    def normalise_tags(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            clean = " ".join(str(value).strip().split())[:80]
            if clean and clean not in result:
                result.append(clean)
        return result[:20]


class ContactRecordCreateResponse(BaseModel):
    ok: bool = True
    contact_id: str
    saved: bool = True
    consent_statement_version: str
    created_at: str


@router.on_event("startup")
def initialise_contact_records() -> None:
    _initialise()


@router.get("/api/contact-records/status")
def contact_record_status() -> dict[str, Any]:
    _initialise()
    with _LOCK, _connect() as connection:
        count = int(connection.execute("SELECT COUNT(*) FROM contact_records").fetchone()[0])
        latest = connection.execute(
            "SELECT created_at FROM contact_records ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return {
        "ok": True,
        "configured": True,
        "record_count": count,
        "latest_created_at": latest["created_at"] if latest else None,
        "database_path": str(_database_path()),
        "pii_returned": False,
    }


@router.post("/api/contact-records", response_model=ContactRecordCreateResponse)
def create_contact_record(req: ContactRecordCreateRequest) -> ContactRecordCreateResponse:
    if not req.contact_consent:
        raise HTTPException(status_code=400, detail="Explicit contact consent is required.")

    email = (req.email or "").strip() or None
    phone = (req.phone or "").strip() or None
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Email or phone is required.")
    if email and not _EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(status_code=400, detail="The email address is not valid.")
    if phone and not _PHONE_PATTERN.fullmatch(phone):
        raise HTTPException(status_code=400, detail="The phone number contains unsupported characters.")

    _initialise()
    contact_id = f"contact_{uuid.uuid4().hex}"
    audit_event_id = f"audit_{uuid.uuid4().hex}"
    created_at = _now_iso()
    with _LOCK, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO contact_records (
                contact_id, conversation_id, visit_id, name, company, email, phone,
                interest_tags_json, notes, contact_consent, consent_statement_version,
                consent_confirmed_at, source, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'active', ?, ?)
            """,
            (
                contact_id,
                req.conversation_id,
                req.visit_id,
                req.name,
                req.company,
                email,
                phone,
                json.dumps(req.interest_tags, ensure_ascii=False),
                req.notes,
                req.consent_statement_version,
                created_at,
                req.source,
                created_at,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO contact_audit_events (
                audit_event_id, contact_id, action, occurred_at, source, details_json
            ) VALUES (?, ?, 'created_with_contact_consent', ?, ?, ?)
            """,
            (
                audit_event_id,
                contact_id,
                created_at,
                req.source,
                json.dumps(
                    {
                        "conversation_id": req.conversation_id,
                        "visit_id": req.visit_id,
                        "consent_statement_version": req.consent_statement_version,
                        "email_present": bool(email),
                        "phone_present": bool(phone),
                        "interest_tag_count": len(req.interest_tags),
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()

    return ContactRecordCreateResponse(
        contact_id=contact_id,
        consent_statement_version=req.consent_statement_version,
        created_at=created_at,
    )
