from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from typing import Any

from fastapi import HTTPException

from app import contact_record_api


_TIMELINE_START_HOUR = 9
_TIMELINE_END_HOUR = 18
_STAFF_FILE = contact_record_api._REPO_ROOT / "config" / "demo_meeting_staff.json"


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _first(item: dict[str, Any], *keys: str, limit: int) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            value = ", ".join(
                _clean(part, limit)
                for part in value.values()
                if _clean(part, limit)
            )
        clean = _clean(value, limit)
        if clean:
            return clean
    return ""


def _stable_staff_id(item: dict[str, Any], name: str, email: str) -> str:
    explicit = _first(item, "staff_id", "id", "key", "employee_id", limit=80)
    if explicit:
        return explicit
    source = email or name
    return f"staff_{hashlib.sha256(source.casefold().encode('utf-8')).hexdigest()[:16]}"


def _configured_staff_catalog() -> list[dict[str, str]]:
    """Load the real exhibition staff catalog from the repository config file.

    No built-in employee fallback is permitted. Availability remains a deterministic
    demo schedule, but every person shown to the visitor must originate from
    config/demo_meeting_staff.json.
    """

    try:
        payload = json.loads(_STAFF_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Meeting staff configuration is missing: {_STAFF_FILE}",
        ) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Meeting staff configuration is invalid: {_STAFF_FILE}",
        ) from exc

    if isinstance(payload, dict):
        items = payload.get("staff")
        if not isinstance(items, list):
            items = payload.get("employees")
        if not isinstance(items, list):
            items = payload.get("team")
    else:
        items = payload

    if not isinstance(items, list):
        raise HTTPException(
            status_code=503,
            detail="demo_meeting_staff.json must contain a staff/employees/team array.",
        )

    result: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        name = _first(raw, "name", "display_name", "full_name", limit=120)
        email = _first(raw, "email", "email_address", "work_email", limit=240)
        if not name:
            continue
        staff_id = _stable_staff_id(raw, name, email)
        if staff_id in seen_ids:
            continue
        seen_ids.add(staff_id)
        result.append(
            {
                "staff_id": staff_id,
                "name": name,
                "role": _first(raw, "role", "title", "position", "job_title", limit=160),
                "address": _first(
                    raw,
                    "address",
                    "office_address",
                    "work_address",
                    "location",
                    limit=300,
                ),
                "email": email,
                "phone": _first(raw, "phone", "phone_number", "mobile", limit=100),
            }
        )

    if not result:
        raise HTTPException(
            status_code=503,
            detail="demo_meeting_staff.json contains no valid employee with a name.",
        )
    return result


def _hour_digest(seed: str, day: date, hour: int) -> bytes:
    return hashlib.sha256(f"{seed}|{day.isoformat()}|{hour:02d}:00".encode("utf-8")).digest()


def _available_hours(seed: str, day: date) -> set[int]:
    hours = list(range(_TIMELINE_START_HOUR, _TIMELINE_END_HOUR))
    ranked = sorted(
        hours,
        key=lambda hour: (_hour_digest(seed, day, hour)[0], hour),
        reverse=True,
    )
    # Six green rows provide a useful exhibition demo while still leaving visibly
    # unavailable white rows in the notebook-style day timeline.
    return set(ranked[: min(6, len(ranked))])


def _hourly_availability_for(day: date) -> list[dict[str, Any]]:
    seed = contact_record_api.os.getenv(
        "SMART_OFFICE_DEMO_SCHEDULE_SEED",
        "expo-2026-smart-office",
    )
    staff = _configured_staff_catalog()
    available_hours = _available_hours(seed, day)
    zone = contact_record_api._timezone()
    now_local = datetime.now(zone)

    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        booked_rows = connection.execute(
            """
            SELECT staff_id, start_at FROM meeting_bookings
            WHERE meeting_date = ? AND status = 'confirmed'
            """,
            (day.isoformat(),),
        ).fetchall()
    booked = {(str(row["staff_id"]), str(row["start_at"])) for row in booked_rows}

    records: list[dict[str, Any]] = []
    for hour in range(_TIMELINE_START_HOUR, _TIMELINE_END_HOUR):
        start_local = datetime.combine(day, time(hour=hour), zone)
        end_local = datetime.combine(day, time(hour=hour + 1), zone)
        digest = _hour_digest(seed, day, hour)
        scheduled = hour in available_hours
        staff_item = staff[digest[1] % len(staff)] if scheduled else None
        # Once an hourly block has started, it is no longer a valid new booking.
        is_past = start_local <= now_local
        is_booked = bool(
            staff_item
            and (staff_item["staff_id"], start_local.isoformat()) in booked
        )
        available = bool(staff_item and not is_past and not is_booked)

        if is_past:
            state = "past"
        elif is_booked:
            state = "booked"
        elif available:
            state = "available"
        else:
            state = "unavailable"

        staff_id = staff_item["staff_id"] if staff_item else ""
        slot_source = f"{seed}|{day.isoformat()}|{staff_id or 'none'}|{hour:02d}:00"
        records.append(
            {
                "slot_id": hashlib.sha256(slot_source.encode("utf-8")).hexdigest()[:20],
                "date": day.isoformat(),
                "start_at": start_local.isoformat(),
                "end_at": end_local.isoformat(),
                "start_label": start_local.strftime("%H:%M"),
                "end_label": end_local.strftime("%H:%M"),
                "staff_id": staff_id,
                "staff_name": staff_item["name"] if staff_item else "",
                "staff_role": staff_item["role"] if staff_item else "",
                "staff_address": staff_item["address"] if staff_item else "",
                "staff_email": staff_item["email"] if staff_item else "",
                "staff_phone": staff_item["phone"] if staff_item else "",
                "timezone": contact_record_api._timezone_name(),
                "availability_source": "configured_staff_demo_schedule",
                "staff_source": "config/demo_meeting_staff.json",
                "slot_state": state,
                "available": available,
            }
        )
    return records


contact_record_api._staff_catalog = _configured_staff_catalog
contact_record_api._availability_for = _hourly_availability_for
contact_record_api._SLOT_TIMES = [
    (f"{hour:02d}:00", f"{hour + 1:02d}:00")
    for hour in range(_TIMELINE_START_HOUR, _TIMELINE_END_HOUR)
]
