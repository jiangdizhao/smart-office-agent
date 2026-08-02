from __future__ import annotations

import hashlib
import json
import random
from datetime import date, datetime, time
from typing import Any

from app import contact_record_api


_TIMELINE_START_HOUR = 9
_TIMELINE_LAST_START_HOUR = 18
_CONFIG_FILE = contact_record_api._REPO_ROOT / "config" / "demo_meeting_staff.json"
_COMPANY_STAFF_ID = "company_meeting_team"
_COMPANY_STAFF_NAME = "Smart Office Team"
_COMPANY_STAFF_ROLE = "Company representative"


def _clean(value: Any, limit: int) -> str:
    if isinstance(value, dict):
        value = ", ".join(
            _clean(part, limit)
            for part in value.values()
            if _clean(part, limit)
        )
    return " ".join(str(value or "").strip().split())[:limit]


def _first(item: dict[str, Any], *keys: str, limit: int) -> str:
    for key in keys:
        clean = _clean(item.get(key), limit)
        if clean:
            return clean
    return ""


def _meeting_contact_address() -> str:
    """Return one configured meeting address without assigning an employee.

    Employee identity is never returned or persisted for the booking. Staff entries
    are used only as interchangeable address sources because the exhibition config
    stores the same office address on each employee.
    """

    configured = _clean(
        contact_record_api.os.getenv("SMART_OFFICE_MEETING_CONTACT_ADDRESS", ""),
        500,
    )
    if configured:
        return configured

    try:
        payload = json.loads(_CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        payload = None

    if isinstance(payload, dict):
        company_address = _first(
            payload,
            "contact_address",
            "meeting_address",
            "company_address",
            "office_address",
            "address",
            "location",
            limit=500,
        )
        if company_address:
            return company_address
        items = payload.get("staff")
        if not isinstance(items, list):
            items = payload.get("employees")
        if not isinstance(items, list):
            items = payload.get("team")
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    addresses: list[str] = []
    if isinstance(items, list):
        for raw in items:
            if not isinstance(raw, dict):
                continue
            address = _first(
                raw,
                "address",
                "office_address",
                "work_address",
                "meeting_address",
                "location",
                limit=500,
            )
            if address and address not in addresses:
                addresses.append(address)

    if addresses:
        return random.SystemRandom().choice(addresses)

    return "Our company will confirm the meeting address when contacting you."


def _company_hourly_availability(day: date) -> list[dict[str, Any]]:
    zone = contact_record_api._timezone()
    now_local = datetime.now(zone)
    address = _meeting_contact_address()

    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        booked_rows = connection.execute(
            """
            SELECT start_at FROM meeting_bookings
            WHERE meeting_date = ? AND status = 'confirmed'
            """,
            (day.isoformat(),),
        ).fetchall()
    booked_start_times = {str(row["start_at"]) for row in booked_rows}

    records: list[dict[str, Any]] = []
    for hour in range(_TIMELINE_START_HOUR, _TIMELINE_LAST_START_HOUR + 1):
        start_local = datetime.combine(day, time(hour=hour), zone)
        end_local = datetime.combine(day, time(hour=hour + 1), zone)
        is_past = start_local <= now_local
        is_booked = start_local.isoformat() in booked_start_times
        available = not is_past and not is_booked
        state = "past" if is_past else "booked" if is_booked else "available"
        slot_source = f"company|{day.isoformat()}|{hour:02d}:00"
        records.append(
            {
                "slot_id": hashlib.sha256(slot_source.encode("utf-8")).hexdigest()[:20],
                "date": day.isoformat(),
                "start_at": start_local.isoformat(),
                "end_at": end_local.isoformat(),
                "start_label": start_local.strftime("%H:%M"),
                "end_label": end_local.strftime("%H:%M"),
                "staff_id": _COMPANY_STAFF_ID,
                "staff_name": "",
                "staff_role": "",
                "staff_address": address,
                "contact_address": address,
                "timezone": contact_record_api._timezone_name(),
                "availability_source": "company_hourly_booking",
                "staff_source": None,
                "slot_state": state,
                "available": available,
            }
        )
    return records


contact_record_api._availability_for = _company_hourly_availability
contact_record_api._SLOT_TIMES = [
    (f"{hour:02d}:00", f"{hour + 1:02d}:00")
    for hour in range(_TIMELINE_START_HOUR, _TIMELINE_LAST_START_HOUR + 1)
]

# Preserve the existing result-center schema with one neutral company identity.
# No real employee name or identifier is assigned to a new appointment.
contact_record_api._DEFAULT_STAFF = [
    {
        "staff_id": _COMPANY_STAFF_ID,
        "name": _COMPANY_STAFF_NAME,
        "role": _COMPANY_STAFF_ROLE,
    }
]
