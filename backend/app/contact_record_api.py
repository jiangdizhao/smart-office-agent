from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path
from threading import RLock
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.conversation_store import conversation_store
from app.result_center_auth import AdminSession, require_result_center_admin

router = APIRouter(tags=["contact-records", "visitor-experience"])
AdminDependency = Annotated[AdminSession, Depends(require_result_center_admin)]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_PATTERN = re.compile(r"^[0-9+()\-\s.]{5,80}$")
_LOCK = RLock()
_DEFAULT_STAFF = [
    {"staff_id": "rico", "name": "Rico", "role": "Smart Office Consultant"},
    {"staff_id": "tom", "name": "Tom", "role": "Solution Engineer"},
    {"staff_id": "alice", "name": "Alice", "role": "Product Specialist"},
    {"staff_id": "yu", "name": "Yu", "role": "Technical Demonstrator"},
]
_SLOT_TIMES = [
    ("09:30", "10:00"),
    ("10:00", "10:30"),
    ("11:00", "11:30"),
    ("13:30", "14:00"),
    ("15:00", "15:30"),
    ("16:00", "16:30"),
]


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


def _timezone_name() -> str:
    return os.getenv("SMART_OFFICE_TIMEZONE", "Australia/Sydney").strip() or "Australia/Sydney"


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(_timezone_name())
    except Exception:
        return ZoneInfo("Australia/Sydney")


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
            CREATE INDEX IF NOT EXISTS idx_contact_records_conversation_id
                ON contact_records(conversation_id);

            CREATE TABLE IF NOT EXISTS contact_audit_events (
                audit_event_id TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL,
                action TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                source TEXT NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY(contact_id) REFERENCES contact_records(contact_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS session_summaries (
                summary_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                visit_id TEXT NOT NULL,
                contact_id TEXT,
                language TEXT NOT NULL DEFAULT 'zh',
                status TEXT NOT NULL DEFAULT 'draft',
                bullet_points_json TEXT NOT NULL,
                interests_json TEXT NOT NULL,
                actions_json TEXT NOT NULL,
                follow_ups_json TEXT NOT NULL,
                source_revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finalized_at TEXT,
                UNIQUE(conversation_id, visit_id)
            );

            CREATE INDEX IF NOT EXISTS idx_session_summaries_contact_id
                ON session_summaries(contact_id);
            CREATE INDEX IF NOT EXISTS idx_session_summaries_visit_id
                ON session_summaries(visit_id);

            CREATE TABLE IF NOT EXISTS meeting_bookings (
                booking_id TEXT PRIMARY KEY,
                contact_id TEXT,
                conversation_id TEXT NOT NULL,
                visit_id TEXT NOT NULL,
                staff_id TEXT NOT NULL,
                staff_name TEXT NOT NULL,
                staff_role TEXT NOT NULL,
                meeting_date TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                timezone TEXT NOT NULL,
                topic TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'confirmed',
                availability_source TEXT NOT NULL DEFAULT 'simulated',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(staff_id, start_at)
            );

            CREATE INDEX IF NOT EXISTS idx_meeting_bookings_contact_id
                ON meeting_bookings(contact_id);
            CREATE INDEX IF NOT EXISTS idx_meeting_bookings_visit_id
                ON meeting_bookings(visit_id);
            CREATE INDEX IF NOT EXISTS idx_meeting_bookings_start_at
                ON meeting_bookings(start_at);
            """
        )


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        clean = " ".join(str(item).strip().split())[:300]
        if clean and clean not in result:
            result.append(clean)
    return result


def _contact_for_context(
    connection: sqlite3.Connection,
    conversation_id: str,
    visit_id: str | None,
) -> str | None:
    if visit_id:
        row = connection.execute(
            """
            SELECT contact_id FROM contact_records
            WHERE visit_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (visit_id,),
        ).fetchone()
        if row:
            return str(row["contact_id"])
    row = connection.execute(
        """
        SELECT contact_id FROM contact_records
        WHERE conversation_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()
    return str(row["contact_id"]) if row else None


def _bind_context_records(
    connection: sqlite3.Connection,
    contact_id: str,
    conversation_id: str,
    visit_id: str | None,
) -> None:
    if visit_id:
        connection.execute(
            """
            UPDATE session_summaries SET contact_id = ?, updated_at = ?
            WHERE contact_id IS NULL AND visit_id = ?
            """,
            (contact_id, _now_iso(), visit_id),
        )
        connection.execute(
            """
            UPDATE meeting_bookings SET contact_id = ?, updated_at = ?
            WHERE contact_id IS NULL AND visit_id = ?
            """,
            (contact_id, _now_iso(), visit_id),
        )
    connection.execute(
        """
        UPDATE session_summaries SET contact_id = ?, updated_at = ?
        WHERE contact_id IS NULL AND conversation_id = ?
        """,
        (contact_id, _now_iso(), conversation_id),
    )
    connection.execute(
        """
        UPDATE meeting_bookings SET contact_id = ?, updated_at = ?
        WHERE contact_id IS NULL AND conversation_id = ?
        """,
        (contact_id, _now_iso(), conversation_id),
    )


def _staff_catalog() -> list[dict[str, str]]:
    configured = os.getenv("SMART_OFFICE_DEMO_STAFF_FILE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = _REPO_ROOT / path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload.get("staff") if isinstance(payload, dict) else payload
            if isinstance(items, list):
                result: list[dict[str, str]] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    staff_id = " ".join(str(item.get("staff_id") or "").split())[:80]
                    name = " ".join(str(item.get("name") or "").split())[:120]
                    role = " ".join(str(item.get("role") or "").split())[:160]
                    if staff_id and name:
                        result.append({"staff_id": staff_id, "name": name, "role": role or "Consultant"})
                if result:
                    return result
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return [dict(item) for item in _DEFAULT_STAFF]


def _availability_for(day: date) -> list[dict[str, Any]]:
    seed = os.getenv("SMART_OFFICE_DEMO_SCHEDULE_SEED", "expo-2026-smart-office")
    digest = hashlib.sha256(f"{seed}|{day.isoformat()}".encode("utf-8")).digest()
    staff = _staff_catalog()
    count = 2 + digest[0] % 3
    slot_indexes: list[int] = []
    cursor = 1
    while len(slot_indexes) < count:
        index = digest[cursor % len(digest)] % len(_SLOT_TIMES)
        if index not in slot_indexes:
            slot_indexes.append(index)
        cursor += 1
    zone = _timezone()
    now_local = datetime.now(zone)
    records: list[dict[str, Any]] = []
    for position, slot_index in enumerate(sorted(slot_indexes)):
        start_text, end_text = _SLOT_TIMES[slot_index]
        start_local = datetime.combine(day, time.fromisoformat(start_text), zone)
        end_local = datetime.combine(day, time.fromisoformat(end_text), zone)
        if end_local <= now_local:
            continue
        staff_item = staff[digest[(position + 9) % len(digest)] % len(staff)]
        slot_id = hashlib.sha256(
            f"{seed}|{day.isoformat()}|{staff_item['staff_id']}|{start_text}".encode("utf-8")
        ).hexdigest()[:20]
        records.append(
            {
                "slot_id": slot_id,
                "date": day.isoformat(),
                "start_at": start_local.isoformat(),
                "end_at": end_local.isoformat(),
                "start_label": start_local.strftime("%H:%M"),
                "end_label": end_local.strftime("%H:%M"),
                "staff_id": staff_item["staff_id"],
                "staff_name": staff_item["name"],
                "staff_role": staff_item["role"],
                "timezone": _timezone_name(),
                "availability_source": "simulated",
            }
        )
    with _LOCK, _connect() as connection:
        booked = {
            (str(row["staff_id"]), str(row["start_at"]))
            for row in connection.execute(
                """
                SELECT staff_id, start_at FROM meeting_bookings
                WHERE meeting_date = ? AND status = 'confirmed'
                """,
                (day.isoformat(),),
            ).fetchall()
        }
    for item in records:
        item["available"] = (item["staff_id"], item["start_at"]) not in booked
    return records


def _clean_message_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:500]


def _summarise_messages(messages: list[dict[str, Any]]) -> dict[str, list[str]]:
    useful: list[tuple[str, str]] = []
    for message in messages[-64:]:
        role = str(message.get("role") or "")
        text = _clean_message_text(message.get("text"))
        if not text or role not in {"user", "assistant"}:
            continue
        if re.search(r"欢迎来到|welcome to our office|本轮未完成|需要重新尝试", text, re.I):
            continue
        useful.append((role, text))

    joined = " ".join(text for _, text in useful).casefold()
    feature_map = [
        (r"powerpoint|ppt|幻灯片", "PowerPoint 语音控制"),
        (r"outlook|邮件|草稿", "Outlook 邮件助手"),
        (r"teams|会议软件", "Teams 与会议协作"),
        (r"录音|recording", "现场录音与总结"),
        (r"登记|联系方式|contact", "访客登记"),
        (r"预约|日历|meeting|calendar", "会议预约"),
        (r"智能接待|虚拟人|sara|接待", "智能接待"),
    ]
    interests = [label for pattern, label in feature_map if re.search(pattern, joined, re.I)]

    actions: list[str] = []
    for role, text in useful:
        if role != "user":
            continue
        if re.search(r"打开|关闭|开始|停止|播放|创建|发送|预约|登记|查看|总结", text, re.I):
            concise = text[:90]
            if concise not in actions:
                actions.append(concise)
        if len(actions) >= 5:
            break

    follow_ups: list[str] = []
    if re.search(r"预约|安排.*会议|meeting", joined, re.I):
        follow_ups.append("跟进已选择或计划中的会议预约。")
    if re.search(r"联系|邮箱|电话|登记", joined, re.I):
        follow_ups.append("根据访客授权信息进行后续联系。")
    if re.search(r"演示|demo|产品", joined, re.I):
        follow_ups.append("准备访客关注功能的后续产品演示。")

    bullets: list[str] = []
    if interests:
        bullets.append(f"访客主要关注：{'、'.join(interests)}。")
    if actions:
        bullets.append(f"本 Session 的主要请求包括：{'；'.join(actions[:3])}。")
    assistant_actions = [
        text[:100]
        for role, text in useful
        if role == "assistant" and re.search(r"已经|已在|已打开|已保存|成功|完成", text)
    ]
    if assistant_actions:
        bullets.append(f"系统已完成或确认：{'；'.join(assistant_actions[:2])}。")
    if follow_ups:
        bullets.append(f"建议后续：{'；'.join(follow_ups)}")
    if not bullets:
        user_turns = sum(1 for role, _ in useful if role == "user")
        bullets.append(
            f"本 Session 共识别到 {user_turns} 次有效访客发言，暂未形成明确产品需求。"
        )
    return {
        "bullet_points": bullets[:8],
        "interests": interests[:12],
        "actions": actions[:12],
        "follow_ups": follow_ups[:8],
    }


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
        return _json_list(values)[:20]


class ContactRecordCreateResponse(BaseModel):
    ok: bool = True
    contact_id: str
    saved: bool = True
    consent_statement_version: str
    created_at: str


class SummaryMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str = Field(..., max_length=4_000)
    timestamp: str | None = Field(default=None, max_length=100)
    source: str | None = Field(default=None, max_length=160)


class SessionSummaryUpsertRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    language: Literal["zh", "en"] = "zh"
    status: Literal["draft", "final"] = "draft"
    messages: list[SummaryMessage] = Field(default_factory=list, max_length=64)
    source_revision: int = Field(default=0, ge=0)


class MeetingBookingRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    date: str = Field(..., min_length=10, max_length=10)
    slot_id: str = Field(..., min_length=8, max_length=80)
    topic: str = Field("Smart Office 产品演示", min_length=1, max_length=240)


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
        _bind_context_records(connection, contact_id, req.conversation_id, req.visit_id)
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
                        "session_summary_and_booking_linking": True,
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


def _summary_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "summary_id": row["summary_id"],
        "conversation_id": row["conversation_id"],
        "visit_id": row["visit_id"],
        "contact_id": row["contact_id"],
        "language": row["language"],
        "status": row["status"],
        "bullet_points": _json_list(row["bullet_points_json"]),
        "interests": _json_list(row["interests_json"]),
        "actions": _json_list(row["actions_json"]),
        "follow_ups": _json_list(row["follow_ups_json"]),
        "source_revision": int(row["source_revision"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finalized_at": row["finalized_at"],
    }


def _upsert_summary(req: SessionSummaryUpsertRequest) -> dict[str, Any]:
    _initialise()
    messages = [item.model_dump() for item in req.messages]
    if not messages:
        snapshot = conversation_store.snapshot(req.conversation_id)
        state = (snapshot or {}).get("messages") if isinstance(snapshot, dict) else None
        messages = state if isinstance(state, list) else []
    summary = _summarise_messages(messages)
    now = _now_iso()
    with _LOCK, _connect() as connection:
        contact_id = _contact_for_context(connection, req.conversation_id, req.visit_id)
        existing = connection.execute(
            """
            SELECT summary_id, created_at FROM session_summaries
            WHERE conversation_id = ? AND visit_id = ?
            """,
            (req.conversation_id, req.visit_id),
        ).fetchone()
        summary_id = str(existing["summary_id"]) if existing else f"summary_{uuid.uuid4().hex}"
        created_at = str(existing["created_at"]) if existing else now
        connection.execute(
            """
            INSERT INTO session_summaries (
                summary_id, conversation_id, visit_id, contact_id, language, status,
                bullet_points_json, interests_json, actions_json, follow_ups_json,
                source_revision, created_at, updated_at, finalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id, visit_id) DO UPDATE SET
                contact_id = COALESCE(excluded.contact_id, session_summaries.contact_id),
                language = excluded.language,
                status = excluded.status,
                bullet_points_json = excluded.bullet_points_json,
                interests_json = excluded.interests_json,
                actions_json = excluded.actions_json,
                follow_ups_json = excluded.follow_ups_json,
                source_revision = excluded.source_revision,
                updated_at = excluded.updated_at,
                finalized_at = excluded.finalized_at
            """,
            (
                summary_id,
                req.conversation_id,
                req.visit_id,
                contact_id,
                req.language,
                req.status,
                json.dumps(summary["bullet_points"], ensure_ascii=False),
                json.dumps(summary["interests"], ensure_ascii=False),
                json.dumps(summary["actions"], ensure_ascii=False),
                json.dumps(summary["follow_ups"], ensure_ascii=False),
                req.source_revision,
                created_at,
                now,
                now if req.status == "final" else None,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM session_summaries WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
    return {"ok": True, "summary": _summary_row(row)}


@router.put("/api/visitor-experience/session-summaries")
def upsert_session_summary(req: SessionSummaryUpsertRequest) -> dict[str, Any]:
    return _upsert_summary(req)


@router.get("/api/visitor-experience/session-summaries/current")
def current_session_summary(
    conversation_id: str = Query(..., min_length=1, max_length=160),
    visit_id: str = Query(..., min_length=1, max_length=160),
    language: Literal["zh", "en"] = Query(default="zh"),
) -> dict[str, Any]:
    _initialise()
    with _LOCK, _connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM session_summaries
            WHERE conversation_id = ? AND visit_id = ?
            """,
            (conversation_id, visit_id),
        ).fetchone()
    if row:
        return {"ok": True, "summary": _summary_row(row)}
    return _upsert_summary(
        SessionSummaryUpsertRequest(
            conversation_id=conversation_id,
            visit_id=visit_id,
            language=language,
            status="draft",
            messages=[],
            source_revision=0,
        )
    )


@router.get("/api/visitor-experience/meeting-availability")
def meeting_availability(
    day: str = Query(..., alias="date", min_length=10, max_length=10),
) -> dict[str, Any]:
    try:
        selected = date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid meeting date.") from exc
    today = datetime.now(_timezone()).date()
    if selected < today:
        raise HTTPException(status_code=400, detail="Past dates cannot be booked.")
    slots = _availability_for(selected)
    return {
        "ok": True,
        "date": selected.isoformat(),
        "timezone": _timezone_name(),
        "simulated": True,
        "slots": slots,
    }


@router.post("/api/visitor-experience/meeting-bookings")
def create_meeting_booking(req: MeetingBookingRequest) -> dict[str, Any]:
    try:
        selected = date.fromisoformat(req.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid meeting date.") from exc
    slot = next((item for item in _availability_for(selected) if item["slot_id"] == req.slot_id), None)
    if slot is None or not slot.get("available"):
        raise HTTPException(status_code=409, detail="This simulated meeting slot is no longer available.")

    _initialise()
    booking_id = f"booking_{uuid.uuid4().hex}"
    now = _now_iso()
    with _LOCK, _connect() as connection:
        contact_id = _contact_for_context(connection, req.conversation_id, req.visit_id)
        try:
            connection.execute(
                """
                INSERT INTO meeting_bookings (
                    booking_id, contact_id, conversation_id, visit_id,
                    staff_id, staff_name, staff_role, meeting_date,
                    start_at, end_at, timezone, topic, status,
                    availability_source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 'simulated', ?, ?)
                """,
                (
                    booking_id,
                    contact_id,
                    req.conversation_id,
                    req.visit_id,
                    slot["staff_id"],
                    slot["staff_name"],
                    slot["staff_role"],
                    selected.isoformat(),
                    slot["start_at"],
                    slot["end_at"],
                    slot["timezone"],
                    " ".join(req.topic.strip().split()),
                    now,
                    now,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="This meeting slot has already been booked.") from exc
        row = connection.execute(
            "SELECT * FROM meeting_bookings WHERE booking_id = ?",
            (booking_id,),
        ).fetchone()
    return {"ok": True, "booking": dict(row), "contact_linked": bool(row["contact_id"])}


def _contact_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "contact_id": row["contact_id"],
        "conversation_id": row["conversation_id"],
        "visit_id": row["visit_id"],
        "name": row["name"],
        "company": row["company"],
        "email": row["email"],
        "phone": row["phone"],
        "interest_tags": _json_list(row["interest_tags_json"]),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _rebind_orphans(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT contact_id, conversation_id, visit_id FROM contact_records"
    ).fetchall()
    for row in rows:
        _bind_context_records(
            connection,
            str(row["contact_id"]),
            str(row["conversation_id"]),
            str(row["visit_id"] or "") or None,
        )


@router.get("/api/result-center/visitor-profiles")
def visitor_profiles(
    _admin: AdminDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict[str, Any]:
    _initialise()
    now = datetime.now(_timezone()).isoformat()
    with _LOCK, _connect() as connection:
        _rebind_orphans(connection)
        connection.commit()
        rows = connection.execute(
            """
            SELECT c.*,
                COUNT(DISTINCT b.booking_id) AS appointment_count,
                COUNT(DISTINCT s.summary_id) AS session_summary_count,
                MIN(CASE WHEN b.status = 'confirmed' AND b.start_at >= ? THEN b.start_at END)
                    AS next_appointment_at,
                MAX(COALESCE(b.updated_at, s.updated_at, c.updated_at)) AS latest_activity_at
            FROM contact_records c
            LEFT JOIN meeting_bookings b ON b.contact_id = c.contact_id
            LEFT JOIN session_summaries s ON s.contact_id = c.contact_id
            WHERE c.status = 'active'
            GROUP BY c.contact_id
            ORDER BY
                CASE WHEN MIN(CASE WHEN b.status = 'confirmed' AND b.start_at >= ? THEN b.start_at END)
                    IS NULL THEN 1 ELSE 0 END ASC,
                next_appointment_at ASC,
                latest_activity_at DESC,
                c.created_at DESC
            LIMIT ?
            """,
            (now, now, max(1, min(1000, int(limit)))),
        ).fetchall()
    profiles = []
    for row in rows:
        contact = _contact_dict(row)
        contact.update(
            {
                "appointment_count": int(row["appointment_count"] or 0),
                "session_summary_count": int(row["session_summary_count"] or 0),
                "next_appointment_at": row["next_appointment_at"],
                "has_upcoming_appointment": bool(row["next_appointment_at"]),
                "latest_activity_at": row["latest_activity_at"] or row["updated_at"],
            }
        )
        profiles.append(contact)
    return {"ok": True, "profiles": profiles, "returned": len(profiles)}


@router.get("/api/result-center/visitor-profiles/{contact_id}")
def visitor_profile_detail(contact_id: str, _admin: AdminDependency) -> dict[str, Any]:
    _initialise()
    with _LOCK, _connect() as connection:
        _rebind_orphans(connection)
        connection.commit()
        contact = connection.execute(
            "SELECT * FROM contact_records WHERE contact_id = ? AND status = 'active'",
            (contact_id,),
        ).fetchone()
        if contact is None:
            raise HTTPException(status_code=404, detail="Visitor profile not found.")
        bookings = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM meeting_bookings
                WHERE contact_id = ?
                ORDER BY start_at ASC
                """,
                (contact_id,),
            ).fetchall()
        ]
        summaries = [
            _summary_row(row)
            for row in connection.execute(
                """
                SELECT * FROM session_summaries
                WHERE contact_id = ?
                ORDER BY updated_at DESC
                """,
                (contact_id,),
            ).fetchall()
        ]
    return {
        "ok": True,
        "contact": _contact_dict(contact),
        "appointments": bookings,
        "session_summaries": summaries,
    }
