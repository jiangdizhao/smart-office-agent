from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.contact_record_api import _connect, _database_path, _initialise
from app.human_recording_api import (
    _ALLOWED_AUDIO_SUFFIXES,
    _AUDIO_PREFIX,
    _SUMMARY_PREFIX,
    _output_directory,
)
from app.result_center_auth import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminSession,
    login_result_center_admin,
    logout_result_center_admin,
    require_result_center_admin,
    result_center_admin_status,
)

router = APIRouter(prefix="/api/result-center", tags=["exhibition-result-center"])
AdminDependency = Annotated[AdminSession, Depends(require_result_center_admin)]


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


def _contacts_payload(limit: int) -> dict[str, Any]:
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


@router.get("/contacts")
def list_contacts(
    _admin: AdminDependency,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    return _contacts_payload(limit)


@router.get("/contacts.csv")
def export_contacts_csv(_admin: AdminDependency) -> Response:
    payload = _contacts_payload(limit=1000)
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
        "artifact_url": f"/api/result-center/artifacts/{path.name}",
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
    _admin: AdminDependency,
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
                "artifact_url": f"/api/result-center/artifacts/{path.name}",
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            }
            for path in summaries
        ],
    }


@router.get("/artifacts/{filename}")
def result_center_artifact(filename: str, _admin: AdminDependency):
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid artifact filename.")
    if not safe_name.startswith((_AUDIO_PREFIX, _SUMMARY_PREFIX)):
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", safe_name):
        raise HTTPException(status_code=404, detail="Artifact not found.")

    output_directory = _output_directory()
    path = (output_directory / safe_name).resolve()
    if output_directory not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")

    media_types = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".json": "application/json",
        ".txt": "text/plain",
        ".webm": "audio/webm",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
    }
    media_type = media_types.get(path.suffix.casefold())
    if media_type is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.post("/open-output-directory")
def open_output_directory(_admin: AdminDependency) -> dict[str, Any]:
    directory = _output_directory()
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Opening Explorer is only supported on Windows.")
    try:
        os.startfile(str(directory))  # type: ignore[attr-defined]
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open the output directory: {exc}") from exc
    return {"ok": True, "opened": True, "output_directory": str(directory)}


@router.post("/admin/login", response_model=AdminLoginResponse)
def result_center_admin_login(
    payload: AdminLoginRequest,
    request: Request,
) -> AdminLoginResponse:
    return login_result_center_admin(payload, request)


@router.get("/admin/status")
def result_center_admin_session_status(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
) -> dict[str, object]:
    return result_center_admin_status(request, authorization, access_token)


@router.post("/admin/logout")
def result_center_admin_logout(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
) -> dict[str, bool]:
    return logout_result_center_admin(request, authorization, access_token)
