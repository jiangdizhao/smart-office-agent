from __future__ import annotations

import csv
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.contact_record_api import _connect, _database_path, _initialise
from app.human_recording_api import (
    _ALLOWED_AUDIO_SUFFIXES,
    _AUDIO_PREFIX,
    _SUMMARY_PREFIX,
    _output_directory,
)

router = APIRouter(prefix="/api/result-center", tags=["exhibition-result-center"])


def _safe_limit(value: int) -> int:
    return max(1, min(1000, int(value)))


def _contact_row(row: Any) -> dict[str, Any]:
    try:
        interests = json.loads(str(row["interest_tags_json"] or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        interests = []
    return {
        "contact_id": row["contact_id"],
        "conversation_id": row["conversation_id"],
        "visit_id": row["visit_id"],
        "name": row["name"],
        "company": row["company"],
        "email": row["email"],
        "phone": row["phone"],
        "interest_tags": interests if isinstance(interests, list) else [],
        "notes": row["notes"],
        "contact_consent": bool(row["contact_consent"]),
        "consent_statement_version": row["consent_statement_version"],
        "consent_confirmed_at": row["consent_confirmed_at"],
        "source": row["source"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/contacts")
def list_contacts(limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
    _initialise()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT contact_id, conversation_id, visit_id, name, company, email, phone,
                   interest_tags_json, notes, contact_consent, consent_statement_version,
                   consent_confirmed_at, source, status, created_at, updated_at
            FROM contact_records
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (_safe_limit(limit),),
        ).fetchall()
        total = int(connection.execute("SELECT COUNT(*) FROM contact_records").fetchone()[0])
    return {
        "ok": True,
        "total": total,
        "returned": len(rows),
        "database_path": str(_database_path()),
        "records": [_contact_row(row) for row in rows],
    }


@router.get("/contacts.csv")
def export_contacts_csv() -> Response:
    payload = list_contacts(limit=1000)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "contact_id",
            "created_at",
            "name",
            "company",
            "email",
            "phone",
            "interest_tags",
            "notes",
            "contact_consent",
            "consent_statement_version",
            "visit_id",
            "conversation_id",
            "status",
        ]
    )
    for record in payload["records"]:
        writer.writerow(
            [
                record["contact_id"],
                record["created_at"],
                record["name"],
                record["company"] or "",
                record["email"] or "",
                record["phone"] or "",
                "; ".join(str(item) for item in record["interest_tags"]),
                record["notes"] or "",
                "yes" if record["contact_consent"] else "no",
                record["consent_statement_version"],
                record["visit_id"] or "",
                record["conversation_id"],
                record["status"],
            ]
        )
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d_%H%M%S")
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="smart_office_contacts_{stamp}.csv"'},
    )


def _recording_metadata(path: Path) -> dict[str, Any]:
    metadata_path = path.with_suffix(path.suffix + ".json")
    metadata: dict[str, Any] = {}
    if metadata_path.is_file():
        try:
            parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                metadata = parsed
        except (OSError, ValueError, json.JSONDecodeError):
            metadata = {}
    stat = path.stat()
    return {
        "filename": path.name,
        "audio_path": str(path.resolve()),
        "artifact_url": f"/api/human-recordings/artifacts/{path.name}",
        "conversation_id": str(metadata.get("conversation_id") or ""),
        "language": str(metadata.get("language") or ""),
        "content_type": str(metadata.get("content_type") or ""),
        "size_bytes": int(metadata.get("size_bytes") or stat.st_size),
        "uploaded_at": str(
            metadata.get("uploaded_at")
            or datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        ),
        "metadata_path": str(metadata_path.resolve()) if metadata_path.is_file() else None,
    }


@router.get("/recordings")
def list_recordings(
    limit: int = Query(default=200, ge=1, le=1000),
    conversation_id: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    directory = _output_directory()
    records: list[dict[str, Any]] = []
    for path in directory.glob(f"{_AUDIO_PREFIX}*"):
        if not path.is_file() or path.suffix.casefold() not in _ALLOWED_AUDIO_SUFFIXES:
            continue
        item = _recording_metadata(path)
        if conversation_id and item["conversation_id"] != conversation_id:
            continue
        records.append(item)
    records.sort(key=lambda item: item["uploaded_at"], reverse=True)
    total = len(records)
    records = records[: _safe_limit(limit)]

    summaries = sorted(
        (
            path
            for path in directory.glob(f"{_SUMMARY_PREFIX}*.docx")
            if path.is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[: _safe_limit(limit)]
    return {
        "ok": True,
        "total": total,
        "returned": len(records),
        "output_directory": str(directory),
        "recordings": records,
        "summaries": [
            {
                "filename": path.name,
                "path": str(path.resolve()),
                "artifact_url": f"/api/human-recordings/artifacts/{path.name}",
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            }
            for path in summaries
        ],
    }


@router.post("/open-output-directory")
def open_output_directory() -> dict[str, Any]:
    directory = _output_directory()
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Opening Explorer is only supported on Windows.")
    try:
        os.startfile(str(directory))  # type: ignore[attr-defined]
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open the output directory: {exc}") from exc
    return {"ok": True, "opened": True, "output_directory": str(directory)}
