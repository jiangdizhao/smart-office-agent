from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.conversation_store import ActorType, conversation_store
from app.event_bus import event_bus
from app.executor import run_task_plan_only
from app.models import ApprovalRequest, TaskSession, ToolResult, VerificationResult
from app.planner import plan_task
from app.presentation_actions import execute_presentation_tool_call
from app.presentation_sequence import (
    SEQUENCE_TOOL_NAME,
    create_presentation_sequence_task,
    run_presentation_sequence_task,
    validate_sequence_arguments,
)
from app.reception_knowledge import reception_knowledge
from app.state_store import state_store
from app.task_graph import build_task_graph, task_graph_event_data
from app.turn_router import ApprovalAction, TurnRoute, classify_turn

router = APIRouter(tags=["agent-turn"])

Language = Literal["zh", "en"]
InputSource = Literal["text", "voice", "touch", "keyboard", "hardware"]
PermissionDecision = Literal["allowed", "denied", "not_required"]

_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")


class RealtimePresentationToolCall(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = Field(default=None, max_length=240)
    source: Literal["gpt_realtime"] = "gpt_realtime"


class TurnRequest(BaseModel):
    conversation_id: str = Field(
        "smart-office-debug",
        min_length=1,
        max_length=160,
    )
    text: str = Field(..., max_length=8_000)
    language: Language = "zh"
    input_source: InputSource = "text"
    actor_context: dict[str, Any] = Field(default_factory=dict)
    active_task_id: str | None = None
    realtime_tool_call: RealtimePresentationToolCall | None = None


class TurnResponse(BaseModel):
    conversation_id: str
    route: TurnRoute
    normalized_text: str
    spoken_text: str
    response_language: Language
    task_id: str | None = None
    task_status: str | None = None
    approval_required: bool = False
    approval_action: ApprovalAction | None = None
    actor_type: ActorType
    scene: Literal["reception", "office", "meeting"]
    permission_decision: PermissionDecision = "not_required"
    source_ids: list[str] = Field(default_factory=list)
    content_url: str | None = None
    route_reason: str = ""
    intent_source: str | None = None
    realtime_tool_call: RealtimePresentationToolCall | None = None
    tool_result: ToolResult | None = None
    verification_result: VerificationResult | None = None
    presentation_status: dict[str, Any] | None = None
    phase: str = "m3a_fusion_phase_3_gate_2b"


def _normalise_text(text: str) -> str:
    return " ".join(text.strip().split())


def _response_language(text: str, requested_language: Language) -> Language:
    cjk_count = len(_CJK_PATTERN.findall(text))
    english_words = _ENGLISH_WORD_PATTERN.findall(text)
    latin_character_count = len("".join(english_words))

    if cjk_count == 0 and english_words:
        return "en"
    if cjk_count > 0 and len(english_words) >= 3 and latin_character_count >= cjk_count * 2:
        return "en"
    if cjk_count > 0:
        return "zh"
    return requested_language


def _guard_spoken_language(text: str, language: Language, *, fallback: str) -> str:
    clean = text.strip()
    if language == "en" and _CJK_PATTERN.search(clean):
        return fallback
    return clean


def _actor_type(actor_context: dict[str, Any]) -> ActorType:
    value = str(actor_context.get("type", "visitor")).casefold()
    if value in {"employee", "operator"}:
        return value  # type: ignore[return-value]
    return "visitor"


def _create_plan_only_task(text: str) -> TaskSession:
    planned_steps = plan_task(text)
    task_graph = build_task_graph(planned_steps)
    task = state_store.create_task(
        user_request=text,
        execute=False,
        task_graph=task_graph,
    )
    event_bus.publish(
        task_id=task.task_id,
        event_type="task_created",
        message="Task session created by the unified turn router.",
        data={
            "execute": False,
            "step_count": len(task_graph.steps),
            "source": "agent_turn_phase2_compatibility",
        },
    )
    event_bus.publish(
        task_id=task.task_id,
        event_type="planning",
        message="Unified router converted the Office goal into a plan-only task graph.",
        data=task_graph_event_data(task_graph),
    )
    asyncio.create_task(run_task_plan_only(task.task_id))
    return task


def _cancel_task(task_id: str) -> tuple[TaskSession | None, bool]:
    task = state_store.get_task(task_id)
    if task is None:
        return None, False
    if task.status in {"completed", "failed", "cancelled"}:
        return task, False
    state_store.update_pending_steps(
        task_id,
        "cancelled",
        message="Task cancellation requested through the unified turn router.",
    )
    state_store.set_status(
        task_id,
        "cancelled",
        summary="Task cancellation requested through the unified turn router.",
    )
    event_bus.publish(
        task_id=task_id,
        event_type="cancelled",
        message="Task cancellation requested through /agent/turn.",
        data={"source": "agent_turn"},
    )
    return state_store.get_task(task_id), True


def _apply_approval_action(
    task_id: str,
    action: ApprovalAction,
) -> tuple[TaskSession | None, bool]:
    if action == "cancel":
        return _cancel_task(task_id)

    task = state_store.get_task(task_id)
    if task is None:
        return None, False
    waiting_step = next(
        (step for step in task.steps if step.status == "waiting_approval"),
        None,
    )
    if waiting_step is None:
        return task, False

    state_store.set_approval(
        task_id,
        waiting_step.step_id,
        ApprovalRequest(action=action, note="Submitted through /agent/turn"),
    )
    return state_store.get_task(task_id), True


def _direct_reply(
    text: str,
    language: Language,
    last_visible_answer: str,
) -> str:
    lowered = text.casefold()
    if not text or text == "__UNCLEAR__":
        return "我没有听清，请再说一次。" if language == "zh" else "I did not catch that. Please try again."

    if lowered in {"停止", "停下", "别说了", "stop", "stop speaking", "cancel speech"}:
        return "好的，我已停止语音输出。" if language == "zh" else "Okay, I have stopped speaking."

    if lowered in {"重复", "再说一遍", "请重复", "repeat", "say that again"}:
        if last_visible_answer:
            return last_visible_answer
        return "目前没有可以重复的上一条答复。" if language == "zh" else "There is no previous answer to repeat."

    if lowered in {"你好", "您好", "hello", "hi", "good morning", "good afternoon"}:
        return (
            "您好，我是 Smart Office 虚拟接待与办公助手。您可以询问公开资料，也可以以员工身份控制当前演示。"
            if language == "zh"
            else "Hello. I am the Smart Office virtual host and office assistant. You can ask about approved public information or control the current presentation as an employee."
        )

    return (
        f"我已理解您的话：{text}。当前请求不需要创建办公任务。"
        if language == "zh"
        else f"I understood: {text}. This request does not require an office task."
    )


def _office_permission_denied(language: Language) -> str:
    return (
        "该请求属于办公操作。当前身份是访客，不能控制 PowerPoint；请切换为员工或操作员身份后再试。"
        if language == "zh"
        else "This is an office action. A visitor cannot control PowerPoint. Switch to an employee or operator identity and try again."
    )


def _presentation_status_reply(status: dict[str, Any], language: Language) -> str:
    if not status.get("presentation_open"):
        return "PowerPoint 当前尚未打开。" if language == "zh" else "PowerPoint is not currently open."

    total = status.get("total_slides")
    if status.get("slideshow_active"):
        current = status.get("current_slide")
        monitor_verified = bool(status.get("monitor_placement_enforced"))
        monitor = status.get("slideshow_monitor_device")
        if language == "zh":
            reply = f"当前是第 {current} 页，共 {total} 页。"
            if monitor_verified and monitor:
                reply += f" 放映屏幕为 {monitor}。"
            return reply
        reply = f"The slide show is currently on slide {current} of {total}."
        if monitor_verified and monitor:
            reply += f" It is displayed on {monitor}."
        return reply

    return (
        f"演示文稿已经打开，共 {total} 页，但尚未开始放映。"
        if language == "zh"
        else f"The presentation is open with {total} slides, but the slide show has not started."
    )


def _presentation_action_reply(
    name: str,
    tool_result: ToolResult,
    verification: VerificationResult,
    status: dict[str, Any],
    language: Language,
) -> str:
    if not tool_result.ok:
        return (
            f"PowerPoint 操作没有执行：{tool_result.message}"
            if language == "zh"
            else f"The PowerPoint action was not executed: {tool_result.message}"
        )

    if name == "presentation_get_status":
        return _presentation_status_reply(status, language)

    if not verification.ok:
        return (
            f"PowerPoint 已收到操作，但实际状态没有通过验证：{verification.message}"
            if language == "zh"
            else f"PowerPoint accepted the action, but the observed state did not pass verification: {verification.message}"
        )

    current = status.get("current_slide")
    total = status.get("total_slides")
    monitor = status.get("slideshow_monitor_device") or status.get("target_monitor_device")
    zh_messages = {
        "presentation_open_configured": f"已打开演示文稿 Loss.pptx，共 {total} 页。",
        "presentation_start_slideshow": f"演示已经开始，并已在 {monitor} 放映。当前是第 {current} 页。",
        "presentation_next_slide": f"已翻到下一页。当前是第 {current} 页，共 {total} 页。",
        "presentation_previous_slide": f"已返回上一页。当前是第 {current} 页，共 {total} 页。",
        "presentation_go_to_slide": f"已跳转到第 {current} 页，共 {total} 页。",
        "presentation_end_slideshow": "演示已经结束。",
    }
    en_messages = {
        "presentation_open_configured": f"Loss.pptx is open with {total} slides.",
        "presentation_start_slideshow": f"The slide show has started on {monitor}. It is on slide {current}.",
        "presentation_next_slide": f"Moved to the next slide. This is slide {current} of {total}.",
        "presentation_previous_slide": f"Moved to the previous slide. This is slide {current} of {total}.",
        "presentation_go_to_slide": f"Moved to slide {current} of {total}.",
        "presentation_end_slideshow": "The slide show has ended.",
    }
    messages = zh_messages if language == "zh" else en_messages
    return messages.get(name, tool_result.message)


def _sequence_validation_verification(error: ToolResult) -> VerificationResult:
    return VerificationResult(
        ok=False,
        message="Compound presentation request failed Backend validation.",
        process_ok=None,
        window_ok=None,
        expected_process_names=error.expected_process_names,
        expected_window_keywords=error.expected_window_keywords,
        checked_at=datetime.now(UTC),
        raw={"validation_error": error.message},
    )


@router.get("/agent/turn/status")
def turn_status() -> dict:
    knowledge_status = reception_knowledge.status()
    return {
        "ok": True,
        "phase": "m3a_fusion_phase_3_gate_2b",
        "routes": [
            "realtime_direct",
            "reception_knowledge",
            "office_direct",
            "office_planned_task",
            "approval_action",
            "clarification",
        ],
        "actor_types": ["visitor", "employee", "operator"],
        "task_creation_enabled": True,
        "office_execution_enabled": False,
        "presentation_execution_enabled": True,
        "compound_presentation_execution_enabled": True,
        "presentation_intent_source": "gpt_realtime_function_call",
        "response_language_policy": "utterance_detected_with_english_output_guard",
        "knowledge_mode": knowledge_status["mode"],
        "knowledge_content_version": knowledge_status["content_version"],
    }


@router.get("/agent/conversations/{conversation_id}")
def conversation_status(conversation_id: str) -> dict:
    snapshot = conversation_store.snapshot(conversation_id)
    return {"ok": snapshot is not None, "conversation": snapshot}


@router.post("/agent/turn", response_model=TurnResponse)
async def handle_turn(req: TurnRequest) -> TurnResponse:
    normalized_text = _normalise_text(req.text)
    response_language = _response_language(normalized_text, req.language)
    actor_type = _actor_type(req.actor_context)
    conversation = conversation_store.get_or_create(
        req.conversation_id,
        language=response_language,
        actor_type=actor_type,
    )
    if req.active_task_id:
        conversation_store.update(
            req.conversation_id,
            active_task_id=req.active_task_id,
            set_active_task=True,
        )
        conversation = conversation_store.get_or_create(
            req.conversation_id,
            language=response_language,
            actor_type=actor_type,
        )

    if req.realtime_tool_call is not None:
        permission_decision: PermissionDecision = "denied" if actor_type == "visitor" else "allowed"
        if permission_decision == "denied":
            spoken_text = _office_permission_denied(response_language)
            spoken_text = _guard_spoken_language(
                spoken_text,
                response_language,
                fallback="A visitor cannot control PowerPoint. Switch to an employee or operator identity and try again.",
            )
            conversation_store.update(
                req.conversation_id,
                current_scene="office",
                last_visible_answer=spoken_text,
                last_command=normalized_text,
            )
            return TurnResponse(
                conversation_id=req.conversation_id,
                route="office_direct",
                normalized_text=normalized_text,
                spoken_text=spoken_text,
                response_language=response_language,
                actor_type=actor_type,
                scene="office",
                permission_decision=permission_decision,
                route_reason="GPT Realtime selected a presentation capability, but the actor permission gate denied execution.",
                intent_source="gpt_realtime_function_call",
                realtime_tool_call=req.realtime_tool_call,
            )

        if req.realtime_tool_call.name == SEQUENCE_TOOL_NAME:
            planned_steps, validation_error = validate_sequence_arguments(
                req.realtime_tool_call.arguments
            )
            if validation_error is not None or planned_steps is None:
                error_result = validation_error or ToolResult(
                    tool_name=SEQUENCE_TOOL_NAME,
                    ok=False,
                    message="Compound presentation sequence is invalid.",
                )
                verification = _sequence_validation_verification(error_result)
                spoken_text = (
                    f"复合演示命令没有执行：{error_result.message}"
                    if response_language == "zh"
                    else f"The compound presentation command was not executed: {error_result.message}"
                )
                return TurnResponse(
                    conversation_id=req.conversation_id,
                    route="office_planned_task",
                    normalized_text=normalized_text,
                    spoken_text=spoken_text,
                    response_language=response_language,
                    actor_type=actor_type,
                    scene="office",
                    permission_decision="allowed",
                    route_reason="GPT Realtime selected a compound presentation sequence, but Backend validation rejected it.",
                    intent_source="gpt_realtime_function_call",
                    realtime_tool_call=req.realtime_tool_call,
                    tool_result=error_result,
                    verification_result=verification,
                )

            task = create_presentation_sequence_task(normalized_text, planned_steps)
            conversation_store.update(
                req.conversation_id,
                current_scene="office",
                active_task_id=task.task_id,
                set_active_task=True,
                last_command=normalized_text,
            )
            asyncio.create_task(run_presentation_sequence_task(task.task_id))
            scheduled_result = ToolResult(
                tool_name=SEQUENCE_TOOL_NAME,
                ok=True,
                message="Compound presentation task validated and scheduled.",
                expected_process_names=["POWERPNT.EXE"],
                expected_window_keywords=["PowerPoint"],
                data={
                    "execution_mode": "real",
                    "task_id": task.task_id,
                    "step_count": len(planned_steps),
                    "requested_state": {"compound_sequence": True},
                },
            )
            spoken_text = (
                f"正在执行包含 {len(planned_steps)} 个步骤的演示任务。"
                if response_language == "zh"
                else f"Executing a presentation task with {len(planned_steps)} steps."
            )
            return TurnResponse(
                conversation_id=req.conversation_id,
                route="office_planned_task",
                normalized_text=normalized_text,
                spoken_text=spoken_text,
                response_language=response_language,
                task_id=task.task_id,
                task_status=task.status,
                actor_type=actor_type,
                scene="office",
                permission_decision="allowed",
                route_reason="GPT Realtime selected a bounded compound presentation sequence; Backend validated it and scheduled the existing task runtime.",
                intent_source="gpt_realtime_function_call",
                realtime_tool_call=req.realtime_tool_call,
                tool_result=scheduled_result,
            )

        tool_result, verification, status_result = await asyncio.to_thread(
            execute_presentation_tool_call,
            req.realtime_tool_call.name,
            req.realtime_tool_call.arguments,
        )
        status = dict(status_result.data)
        spoken_text = _presentation_action_reply(
            req.realtime_tool_call.name,
            tool_result,
            verification,
            status,
            response_language,
        )
        spoken_text = _guard_spoken_language(
            spoken_text,
            response_language,
            fallback="The PowerPoint request was processed. Please check the verified presentation status shown on screen.",
        )
        conversation_store.update(
            req.conversation_id,
            current_scene="office",
            last_visible_answer=spoken_text,
            last_command=normalized_text,
        )
        return TurnResponse(
            conversation_id=req.conversation_id,
            route="office_direct",
            normalized_text=normalized_text,
            spoken_text=spoken_text,
            response_language=response_language,
            actor_type=actor_type,
            scene="office",
            permission_decision=permission_decision,
            route_reason="GPT Realtime selected a registered Gate 2B presentation capability; Backend validated, executed, and verified it.",
            intent_source="gpt_realtime_function_call",
            realtime_tool_call=req.realtime_tool_call,
            tool_result=tool_result,
            verification_result=verification,
            presentation_status=status,
        )

    decision = classify_turn(normalized_text, actor_type)
    spoken_text = ""
    task_id: str | None = None
    task_status: str | None = None
    approval_required = False
    permission_decision: PermissionDecision = "not_required"
    source_ids: list[str] = []
    content_url: str | None = None

    if decision.route == "clarification":
        spoken_text = _direct_reply(normalized_text, response_language, conversation.last_visible_answer)

    elif decision.route == "realtime_direct":
        spoken_text = _direct_reply(normalized_text, response_language, conversation.last_visible_answer)

    elif decision.route == "reception_knowledge":
        match = reception_knowledge.search(normalized_text, response_language)
        spoken_text = match.answer
        source_ids = [match.source_id]
        content_url = f"/reception/content/{match.entry.entry_id}?lang={response_language}"
        permission_decision = "allowed"

    elif decision.route in {"office_direct", "office_planned_task"}:
        if actor_type == "visitor":
            spoken_text = _office_permission_denied(response_language)
            permission_decision = "denied"
        elif decision.route == "office_direct":
            permission_decision = "allowed"
            spoken_text = (
                "该办公请求没有携带 GPT Realtime 的受控 PowerPoint Function Call，因此没有执行。请重新说出明确的演示命令。"
                if response_language == "zh"
                else "This office request did not include a controlled GPT Realtime PowerPoint function call, so it was not executed. Please state a clear presentation command."
            )
        else:
            permission_decision = "allowed"
            task = _create_plan_only_task(normalized_text)
            task_id = task.task_id
            task_status = task.status
            approval_required = any(step.requires_confirmation for step in task.steps)
            conversation_store.update(
                req.conversation_id,
                active_task_id=task.task_id,
                set_active_task=True,
            )
            spoken_text = (
                "该请求不是受控的 PowerPoint 复合命令，因此只创建规划任务，没有执行真实 Office 操作。"
                if response_language == "zh"
                else "This request is not a bounded compound PowerPoint command, so it remains plan-only and no real Office action was executed."
            )

    elif decision.route == "approval_action":
        permission_decision = "allowed" if actor_type in {"employee", "operator"} else "denied"
        if permission_decision == "denied":
            spoken_text = _office_permission_denied(response_language)
        else:
            target_task_id = req.active_task_id or conversation.active_task_id
            if not target_task_id or decision.approval_action is None:
                spoken_text = (
                    "当前没有可处理的活动任务。"
                    if response_language == "zh"
                    else "There is no active task to update."
                )
            else:
                task, applied = _apply_approval_action(target_task_id, decision.approval_action)
                task_id = target_task_id
                task_status = task.status if task else None
                if applied:
                    spoken_text = (
                        f"已提交任务操作：{decision.approval_action}。"
                        if response_language == "zh"
                        else f"Task action submitted: {decision.approval_action}."
                    )
                    if decision.approval_action in {"cancel", "takeover"}:
                        conversation_store.update(
                            req.conversation_id,
                            active_task_id=None,
                            set_active_task=True,
                        )
                else:
                    spoken_text = (
                        "任务已经结束，当前没有可应用的任务操作。"
                        if response_language == "zh"
                        else "The task has already ended, so there is no applicable task action."
                    )

    spoken_text = _guard_spoken_language(
        spoken_text,
        response_language,
        fallback="The response could not be provided entirely in English. Please repeat the request.",
    )
    conversation_store.update(
        req.conversation_id,
        current_scene=decision.scene,
        last_visible_answer=spoken_text,
        last_command=normalized_text,
    )

    return TurnResponse(
        conversation_id=req.conversation_id,
        route=decision.route,
        normalized_text=normalized_text,
        spoken_text=spoken_text,
        response_language=response_language,
        task_id=task_id,
        task_status=task_status,
        approval_required=approval_required,
        approval_action=decision.approval_action,
        actor_type=actor_type,
        scene=decision.scene,
        permission_decision=permission_decision,
        source_ids=source_ids,
        content_url=content_url,
        route_reason=decision.reason,
    )
