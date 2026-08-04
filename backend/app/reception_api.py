from __future__ import annotations

from html import escape
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.conversation_store import ActorType, Language, conversation_store
from app.event_bus import event_bus
from app.reception_knowledge import reception_knowledge
from app.sales_experience import (
    SalesExperienceProactiveRequest,
    SalesExperienceProactiveResponse,
    sales_experience,
)
from app.state_store import state_store
from app.visitor_memory_queue import visitor_memory_queue
from app.visitor_memory_store import visitor_memory_store

router = APIRouter(tags=["reception"])


class ConversationTurnStartRequest(BaseModel):
    language: Language = "zh"
    actor_type: ActorType = "visitor"
    text: str = Field(..., max_length=8_000)
    source: str = Field("unknown", max_length=80)
    visit_id: str | None = Field(default=None, max_length=160)


class ConversationTurnCompleteRequest(BaseModel):
    text: str = Field(..., max_length=12_000)
    route: str | None = Field(default=None, max_length=120)
    task_id: str | None = Field(default=None, max_length=240)
    expect_reply: bool = True
    source: str = Field("agent", max_length=80)
    visit_id: str | None = Field(default=None, max_length=160)


class ConversationTaskStateRequest(BaseModel):
    task_id: str | None = Field(default=None, max_length=240)
    active: bool
    final_text: str = Field("", max_length=12_000)
    visit_id: str | None = Field(default=None, max_length=160)


class VisitEndRequest(BaseModel):
    visitor_session_id: str | None = Field(default=None, max_length=160)
    identity_id: str | None = Field(default=None, max_length=160)
    display_name: str | None = Field(default=None, max_length=120)
    language: Language = "zh"
    reason: str = Field("primary_absent", max_length=120)


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
    track_id: int | None = Field(default=None, ge=1)
    visitor_session_id: str | None = Field(default=None, max_length=160)
    visit_id: str | None = Field(default=None, max_length=160)
    provisional_session_id: str | None = Field(default=None, max_length=160)
    session_stable: bool | None = None
    session_age_seconds: float | None = Field(default=None, ge=0.0)
    session_recovery_count: int | None = Field(default=None, ge=0)
    returning_visitor: bool = False
    greeting_kind: Literal[
        "new_anonymous",
        "returning_anonymous",
        "registered_identity",
    ] = "new_anonymous"
    identity_id: str | None = Field(default=None, max_length=160)
    display_name: str | None = Field(default=None, max_length=80)
    identity_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)


class SalesExperienceOutputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    result: Literal["completed", "interrupted", "failed", "cancelled"]
    cancel_reason: str | None = Field(default=None, max_length=160)


def _stale_visit_http(exc: RuntimeError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


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
    try:
        state = conversation_store.begin_user_turn(
            conversation_id,
            language=request.language,
            actor_type=request.actor_type,
            text=request.text,
            source=request.source,
            expected_visit_id=request.visit_id,
        )
    except RuntimeError as exc:
        raise _stale_visit_http(exc) from exc
    return {
        "ok": True,
        "conversation_phase": state.conversation_phase,
        "visit_id": state.visit_id,
        "revision": state.revision,
    }


@router.post("/api/conversations/{conversation_id}/turn-complete")
def conversation_turn_complete(
    conversation_id: str,
    request: ConversationTurnCompleteRequest,
) -> dict:
    try:
        state = conversation_store.complete_assistant_turn(
            conversation_id,
            text=request.text,
            route=request.route,
            task_id=request.task_id,
            expect_reply=request.expect_reply,
            source=request.source,
            expected_visit_id=request.visit_id,
        )
    except RuntimeError as exc:
        raise _stale_visit_http(exc) from exc
    return {
        "ok": True,
        "conversation_phase": state.conversation_phase,
        "visit_id": state.visit_id,
        "revision": state.revision,
    }


@router.post("/api/conversations/{conversation_id}/task-state")
def conversation_task_state(
    conversation_id: str,
    request: ConversationTaskStateRequest,
) -> dict:
    try:
        state = conversation_store.set_task_state(
            conversation_id,
            task_id=request.task_id,
            active=request.active,
            final_text=request.final_text,
            expected_visit_id=request.visit_id,
        )
    except RuntimeError as exc:
        raise _stale_visit_http(exc) from exc
    return {
        "ok": True,
        "conversation_phase": state.conversation_phase,
        "visit_id": state.visit_id,
        "revision": state.revision,
    }


@router.post("/api/conversations/{conversation_id}/standby")
def conversation_standby(conversation_id: str) -> dict:
    state = conversation_store.mark_standby(conversation_id)
    return {"ok": True, "conversation_phase": state.conversation_phase}


@router.post("/api/conversations/{conversation_id}/visit-end")
def conversation_visit_end(
    conversation_id: str,
    request: VisitEndRequest,
) -> dict:
    visit_id = str(request.visitor_session_id or "").strip() or None
    archive = conversation_store.end_visit(conversation_id, visit_id=visit_id)

    cancelled_task_ids = state_store.cancel_tasks_for_visit(
        conversation_id=conversation_id,
        visit_id=visit_id,
        reason="Owning Visit ended; stale task output is fenced from future visitors.",
    )
    for task_id in cancelled_task_ids:
        event_bus.publish(
            task_id=task_id,
            event_type="cancelled",
            message="Task cancelled because its owning Visit ended.",
            data={
                "conversation_id": conversation_id,
                "visit_id": visit_id,
                "reason": request.reason,
            },
        )

    identity_id = str(request.identity_id or archive.get("identity_id") or "").strip()
    display_name = str(request.display_name or archive.get("display_name") or "").strip()
    memory_queued = False
    memory_job_id: str | None = None
    if archive.get("ended") and identity_id:
        memory_queued, memory_job_id = visitor_memory_queue.enqueue(
            identity_id=identity_id,
            display_name=display_name or identity_id,
            memory_summary=str(archive.get("conversation_summary") or ""),
            recent_messages=list(archive.get("recent_messages") or []),
            visit_id=str(archive.get("visit_id") or visit_id or "") or None,
        )
    if visit_id:
        sales_experience.end_visit(conversation_id, visit_id)

    return {
        "ok": True,
        "accepted": True,
        "conversation_phase": "standby",
        "visit_id": archive.get("visit_id") or visit_id,
        "identity_id": identity_id or None,
        "memory_saved": False,
        "memory_queued": memory_queued,
        "memory_job_id": memory_job_id,
        "memory_queue": visitor_memory_queue.status(),
        "anonymous_history_discarded": not bool(identity_id),
        "cancelled_task_ids": cancelled_task_ids,
        "end_reason": request.reason,
        "ended": bool(archive.get("ended")),
        "archive_reason": archive.get("reason"),
    }


@router.post("/api/conversations/{conversation_id}/proximity-greeting")
def proximity_greeting(
    conversation_id: str,
    request: ProximityDetectionRequest,
) -> dict:
    detection: dict[str, Any] = request.model_dump(
        exclude={"language", "actor_type"},
        mode="json",
    )
    registered_memory = (
        visitor_memory_store.load(request.identity_id)
        if request.greeting_kind == "registered_identity" and request.identity_id
        else None
    )
    triggered, _, reason, state = conversation_store.proximity_greeting(
        conversation_id,
        language=request.language,
        actor_type=request.actor_type,
        detection=detection,
        registered_memory=registered_memory,
    )

    opening = None
    spoken_text = ""
    if triggered and state.visit_id:
        opening = sales_experience.plan_opening(
            conversation_id=conversation_id,
            visit_id=state.visit_id,
            language=request.language,
            greeting_kind=request.greeting_kind,
            display_name=request.display_name or state.display_name,
        )
        spoken_text = opening.text
        state = conversation_store.complete_assistant_turn(
            conversation_id,
            text=spoken_text,
            route="sales_experience_opening",
            expect_reply=True,
            source="virtual_host",
            expected_visit_id=state.visit_id,
        )

    return {
        "ok": True,
        "triggered": triggered,
        "greeting": spoken_text,
        "opening": None if opening is None else opening.model_dump(mode="json"),
        "reason": reason,
        "conversation_phase": state.conversation_phase,
        "proactive_reception": triggered,
        "registered_return": request.greeting_kind == "registered_identity",
        "visit_id": state.visit_id,
        "identity_id": state.identity_id,
        "registered_memory_loaded": bool(registered_memory),
        "revision": state.revision,
    }


@router.post(
    "/api/sales/experience/proactive",
    response_model=SalesExperienceProactiveResponse,
)
def sales_experience_proactive(
    request: SalesExperienceProactiveRequest,
) -> SalesExperienceProactiveResponse:
    return sales_experience.plan_proactive(request)


@router.get("/api/sales/experience/status/{conversation_id}/{visit_id}")
def sales_experience_status(conversation_id: str, visit_id: str) -> dict:
    return sales_experience.status(conversation_id, visit_id)


@router.get("/api/sales/experience/self-test")
def sales_experience_self_test() -> dict:
    return sales_experience.self_test()


@router.post("/api/sales/experience/output-result")
def sales_experience_output_result(request: SalesExperienceOutputRequest) -> dict:
    sales_experience.mark_output_result(
        request.conversation_id,
        request.visit_id,
        request.result,
    )
    if request.cancel_reason:
        sales_experience.mark_cancel_reason(
            request.conversation_id,
            request.visit_id,
            request.cancel_reason,
        )
    return {"ok": True, "phase": "phase1_sales_experience"}


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
