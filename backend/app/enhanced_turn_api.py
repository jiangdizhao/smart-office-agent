from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.conversation_store import conversation_store
from app.general_chat_api import generate_general_chat_answer
from app.turn_api import (
    TurnRequest,
    TurnResponse,
    _actor_type,
    _normalise_text,
    _response_language,
    handle_turn,
)
from app.turn_router import classify_turn

router = APIRouter(tags=["enhanced-agent-turn"])


class EnhancedTurnRequest(TurnRequest):
    visit_id: str | None = Field(default=None, max_length=160)


@router.post("/agent/turn", response_model=TurnResponse)
async def enhanced_handle_turn(req: EnhancedTurnRequest) -> TurnResponse:
    normalized_text = _normalise_text(req.text)
    response_language = _response_language(normalized_text, req.language)
    actor_type = _actor_type(req.actor_context)
    decision = classify_turn(normalized_text, actor_type)
    expected_visit_id = str(
        req.visit_id or conversation_store.current_visit_id(req.conversation_id) or ""
    ).strip() or None

    if req.visit_id and not conversation_store.is_current_visit(
        req.conversation_id,
        req.visit_id,
    ):
        raise HTTPException(status_code=409, detail="stale_visit_before_turn")

    if req.realtime_tool_call is not None or decision.reason != "general_direct_conversation":
        response = await handle_turn(req)
        if expected_visit_id and not conversation_store.is_current_visit(
            req.conversation_id,
            expected_visit_id,
        ):
            raise HTTPException(status_code=409, detail="stale_visit_after_turn")
        return response

    conversation_store.get_or_create(
        req.conversation_id,
        language=response_language,
        actor_type=actor_type,
    )
    try:
        spoken_text, model = await generate_general_chat_answer(
            conversation_id=req.conversation_id,
            text=normalized_text,
            language=response_language,
            actor_type=actor_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Backend general chat failed: {exc}") from exc

    if expected_visit_id and not conversation_store.is_current_visit(
        req.conversation_id,
        expected_visit_id,
    ):
        raise HTTPException(status_code=409, detail="stale_visit_after_general_turn")

    try:
        conversation_store.update(
            req.conversation_id,
            current_scene="reception",
            last_visible_answer=spoken_text,
            last_command=normalized_text,
            expected_visit_id=expected_visit_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return TurnResponse(
        conversation_id=req.conversation_id,
        route="realtime_direct",
        normalized_text=normalized_text,
        spoken_text=spoken_text,
        response_language=response_language,
        actor_type=actor_type,
        scene="reception",
        permission_decision="not_required",
        route_reason=f"General conversation answered by Backend LLM model {model}.",
        intent_source="backend_general_chat_llm",
    )
