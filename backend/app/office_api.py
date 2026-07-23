from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.office_actions import get_office_status
from app.office_artifacts import office_artifact_status
from app.presentation_config import presentation_config

router = APIRouter(tags=["office-runtime"])


@router.get("/api/office/status")
def office_status() -> dict:
    status = get_office_status()
    return {
        "ok": status.ok,
        "status": status.data,
        "artifacts": office_artifact_status(),
        "email_send_enabled": False,
    }


@router.get("/api/office/artifacts/{filename}")
def office_artifact(filename: str):
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid artifact filename.")
    if not safe_name.startswith("presentation_summary_"):
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if Path(safe_name).suffix.casefold() not in {".md", ".json"}:
        raise HTTPException(status_code=404, detail="Artifact not found.")

    output_directory = presentation_config.output_directory.resolve()
    path = (output_directory / safe_name).resolve()
    if output_directory not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")

    media_type = "application/json" if path.suffix.casefold() == ".json" else "text/markdown"
    return FileResponse(path, media_type=media_type, filename=path.name)
