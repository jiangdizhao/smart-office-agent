from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.conversation_store import ActorType, Language, conversation_store
from app.reception_knowledge import reception_knowledge

router = APIRouter(tags=["reception"])


class ConversationTurnStartRequest(BaseModel):
    language: Language = "zh"
    actor_type: ActorType = "visitor"
    text: str = Field(..., max_length=8_000)
    source: str = Field("unknown", max_length=80)


class ConversationTurnCompleteRequest(BaseModel):
    text: str = Field(..., max_length=12_000)
    route: str | None = Field(default=None, max_length=120)
    task_id: str | None = Field(default=None, max_length=240)
    expect_reply: bool = True
    source: str = Field("agent", max_length=80)


class ConversationTaskStateRequest(BaseModel):
    task_id: str | None = Field(default=None, max_length=240)
    active: bool
    final_text: str = Field("", max_length=12_000)


class ProximityDetectionRequest(BaseModel):
    language: Language = "zh"
    actor_type: ActorType = "visitor"
    face_area_ratio: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    frontal_score: float = Field(..., ge=0.0, le=1.0)
    center_x: float = Field(..., ge=0.0, le=1.0)
    center_y: float = Field(..., ge=0.0, le=1.0)
    stable_frames: int = Field(..., ge=1, le=120)
    detector: str = Field("unknown", max_length=80)


@router.get("/api/reception/status")
def reception_status() -> dict:
    return reception_knowledge.status()


@router.get("/api/reception/content")
def list_reception_content() -> dict:
    status = reception_knowledge.status()
    return {
        "ok": True,
        "content_version": status["content_version"],
        "updated_at": status["updated_at"],
        "entries": [
            {
                "id": entry.entry_id,
                "category": entry.category,
                "title": entry.title,
                "public": entry.public,
                "content_url": f"/reception/content/{entry.entry_id}",
            }
            for entry in reception_knowledge.list_public()
        ],
    }


@router.get("/api/reception/content/{entry_id}")
def get_reception_content(entry_id: str) -> dict:
    entry = reception_knowledge.get_entry(entry_id)
    if entry is None or not entry.public:
        raise HTTPException(status_code=404, detail=f"Reception content not found: {entry_id}")
    status = reception_knowledge.status()
    return {
        "ok": True,
        "id": entry.entry_id,
        "category": entry.category,
        "title": entry.title,
        "answer": entry.answer,
        "content_version": status["content_version"],
        "updated_at": status["updated_at"],
    }


@router.get("/api/conversations/{conversation_id}")
def conversation_state(
    conversation_id: str,
    language: Language = "zh",
    actor_type: ActorType = "visitor",
) -> dict:
    return {
        "ok": True,
        "state": conversation_store.context_snapshot(
            conversation_id,
            language=language,
            actor_type=actor_type,
        ),
    }


@router.post("/api/conversations/{conversation_id}/turn-start")
def conversation_turn_start(
    conversation_id: str,
    request: ConversationTurnStartRequest,
) -> dict:
    state = conversation_store.begin_user_turn(
        conversation_id,
        language=request.language,
        actor_type=request.actor_type,
        text=request.text,
        source=request.source,
    )
    return {"ok": True, "conversation_phase": state.conversation_phase}


@router.post("/api/conversations/{conversation_id}/turn-complete")
def conversation_turn_complete(
    conversation_id: str,
    request: ConversationTurnCompleteRequest,
) -> dict:
    state = conversation_store.complete_assistant_turn(
        conversation_id,
        text=request.text,
        route=request.route,
        task_id=request.task_id,
        expect_reply=request.expect_reply,
        source=request.source,
    )
    return {"ok": True, "conversation_phase": state.conversation_phase}


@router.post("/api/conversations/{conversation_id}/task-state")
def conversation_task_state(
    conversation_id: str,
    request: ConversationTaskStateRequest,
) -> dict:
    state = conversation_store.set_task_state(
        conversation_id,
        task_id=request.task_id,
        active=request.active,
        final_text=request.final_text,
    )
    return {"ok": True, "conversation_phase": state.conversation_phase}


@router.post("/api/conversations/{conversation_id}/standby")
def conversation_standby(conversation_id: str) -> dict:
    state = conversation_store.mark_standby(conversation_id)
    return {"ok": True, "conversation_phase": state.conversation_phase}


@router.post("/api/conversations/{conversation_id}/proximity-greeting")
def proximity_greeting(
    conversation_id: str,
    request: ProximityDetectionRequest,
) -> dict:
    detection: dict[str, Any] = request.model_dump(
        exclude={"language", "actor_type"},
        mode="json",
    )
    triggered, greeting, reason, state = conversation_store.proximity_greeting(
        conversation_id,
        language=request.language,
        actor_type=request.actor_type,
        detection=detection,
    )
    return {
        "ok": True,
        "triggered": triggered,
        "greeting": greeting,
        "reason": reason,
        "conversation_phase": state.conversation_phase,
    }


@router.get("/reception/content/{entry_id}", response_class=HTMLResponse)
def reception_content_page(entry_id: str, lang: str = "zh") -> HTMLResponse:
    entry = reception_knowledge.get_entry(entry_id)
    if entry is None or not entry.public:
        raise HTTPException(status_code=404, detail=f"Reception content not found: {entry_id}")

    language = "en" if lang.casefold().startswith("en") else "zh"
    title = entry.title.get(language, entry.title.get("en", entry.entry_id))
    answer = entry.answer.get(language, entry.answer.get("en", ""))
    status = reception_knowledge.status()
    disclaimer = reception_knowledge.disclaimer(language)

    html = f"""<!doctype html>
<html lang="{language}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, Segoe UI, system-ui, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; background: radial-gradient(circle at top, #183260, #07111f 70%); color: #eef4ff; display: grid; place-items: center; }}
    main {{ width: min(920px, calc(100vw - 48px)); border: 1px solid rgba(150,180,230,.3); border-radius: 24px; background: rgba(10,24,48,.92); box-shadow: 0 28px 80px rgba(0,0,0,.45); padding: 42px; }}
    .kicker {{ color: #88b5ff; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; font-size: 12px; }}
    h1 {{ margin: 12px 0 22px; font-size: clamp(32px, 5vw, 58px); line-height: 1.05; }}
    .answer {{ font-size: clamp(20px, 2.6vw, 30px); line-height: 1.65; color: #dce8fb; }}
    footer {{ margin-top: 34px; padding-top: 20px; border-top: 1px solid rgba(150,180,230,.18); color: #879bbd; font-size: 13px; line-height: 1.6; }}
  </style>
</head>
<body>
  <main>
    <div class="kicker">Smart Office Reception Content</div>
    <h1>{escape(title)}</h1>
    <div class="answer">{escape(answer)}</div>
    <footer>
      Source: company_profile:{escape(entry.entry_id)}<br />
      Version: {escape(str(status['content_version']))} · Updated: {escape(str(status['updated_at']))}<br />
      {escape(disclaimer)}
    </footer>
  </main>
</body>
</html>"""
    return HTMLResponse(content=html)
