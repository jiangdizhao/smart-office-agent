from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.conversation_store import ActorType, conversation_store
from app.event_bus import event_bus
from app.executor import run_task_plan_only
from app.models import ApprovalRequest, TaskSession
from app.planner import plan_task
from app.reception_knowledge import reception_knowledge
from app.state_store import state_store
from app.task_graph import build_task_graph, task_graph_event_data
from app.turn_router import ApprovalAction, TurnRoute, classify_turn

router = APIRouter(tags=["agent-turn"])

Language = Literal["zh", "en"]
InputSource = Literal["text", "voice", "touch", "keyboard", "hardware"]
PermissionDecision = Literal["allowed", "denied", "not_required"]


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


class TurnResponse(BaseModel):
    conversation_id: str
    route: TurnRoute
    normalized_text: str
    spoken_text: str
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
    phase: str = "m3a_fusion_phase_2"


def _normalise_text(text: str) -> str:
    return " ".join(text.strip().split())


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
        message="Task session created by the Phase 2 unified turn router.",
        data={
            "execute": False,
            "step_count": len(task_graph.steps),
            "source": "agent_turn_phase2",
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


def _cancel_task(task_id: str) -> TaskSession | None:
    task = state_store.get_task(task_id)
    if task is None:
        return None
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
        data={"source": "agent_turn_phase2"},
    )
    return state_store.get_task(task_id)


def _apply_approval_action(
    task_id: str,
    action: ApprovalAction,
) -> tuple[TaskSession | None, bool]:
    if action == "cancel":
        return _cancel_task(task_id), True

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
            "您好，我是 Smart Office 虚拟接待与办公助手。您可以询问公开资料，也可以以员工身份提出办公任务。"
            if language == "zh"
            else "Hello. I am the Smart Office virtual host and office assistant. You can ask about approved public information or submit office goals as an employee."
        )

    return (
        f"我已理解您的话：{text}。当前请求不需要创建办公任务。"
        if language == "zh"
        else f"I understood: {text}. This request does not require an office task."
    )


def _office_permission_denied(language: Language) -> str:
    return (
        "该请求属于办公操作。当前身份是访客，不能创建或执行 Office 任务；请由员工或操作员确认身份后再试。"
        if language == "zh"
        else "This is an office action. The current actor is a visitor and cannot create or execute Office tasks. An employee or operator must confirm their identity first."
    )


@router.get("/agent/turn/status")
def turn_status() -> dict:
    knowledge_status = reception_knowledge.status()
    return {
        "ok": True,
        "phase": "m3a_fusion_phase_2",
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
    actor_type = _actor_type(req.actor_context)
    conversation = conversation_store.get_or_create(
        req.conversation_id,
        language=req.language,
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
            language=req.language,
            actor_type=actor_type,
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
        spoken_text = _direct_reply(normalized_text, req.language, conversation.last_visible_answer)

    elif decision.route == "realtime_direct":
        spoken_text = _direct_reply(normalized_text, req.language, conversation.last_visible_answer)

    elif decision.route == "reception_knowledge":
        match = reception_knowledge.search(normalized_text, req.language)
        spoken_text = match.answer
        source_ids = [match.source_id]
        content_url = f"/reception/content/{match.entry.entry_id}?lang={req.language}"
        permission_decision = "allowed"

    elif decision.route in {"office_direct", "office_planned_task"}:
        if actor_type == "visitor":
            spoken_text = _office_permission_denied(req.language)
            permission_decision = "denied"
        elif decision.route == "office_direct":
            permission_decision = "allowed"
            spoken_text = (
                f"已识别为办公操作：{normalized_text}。Phase 2 只完成路由和权限判断，尚未执行真实 Office 操作。"
                if req.language == "zh"
                else f"This was classified as an office action: {normalized_text}. Phase 2 performs routing and permission checks only; no real Office action was executed."
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
                "我已创建一个仅规划的办公任务。系统会展示步骤，但 Phase 2 不会执行真实 Office 操作。"
                if req.language == "zh"
                else "I created a plan-only office task. The system will show the steps, but Phase 2 will not execute real Office actions."
            )

    elif decision.route == "approval_action":
        permission_decision = "allowed" if actor_type in {"employee", "operator"} else "denied"
        if permission_decision == "denied":
            spoken_text = _office_permission_denied(req.language)
        else:
            target_task_id = req.active_task_id or conversation.active_task_id
            if not target_task_id or decision.approval_action is None:
                spoken_text = (
                    "当前没有可处理的活动任务。"
                    if req.language == "zh"
                    else "There is no active task to update."
                )
            else:
                task, applied = _apply_approval_action(target_task_id, decision.approval_action)
                task_id = target_task_id
                task_status = task.status if task else None
                if applied:
                    spoken_text = (
                        f"已提交任务操作：{decision.approval_action}。"
                        if req.language == "zh"
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
                        "任务存在，但目前没有正在等待该确认的步骤。"
                        if req.language == "zh"
                        else "The task exists, but no step is currently waiting for that approval action."
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
