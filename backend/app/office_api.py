from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.conversation_store import ActorType, conversation_store
from app.models import ToolResult, VerificationResult
from app.office_actions import execute_office_tool_call, get_office_status
from app.office_artifacts import office_artifact_status
from app.office_sequence import (
    OFFICE_PLAN_TOOL_NAME,
    create_office_task,
    run_office_task,
    validate_office_plan,
)
from app.presentation_config import presentation_config
from app.state_store import state_store

router = APIRouter(tags=["office-runtime"])

Language = Literal["zh", "en"]
InputSource = Literal["text", "voice", "touch", "keyboard", "hardware"]
_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")


class RealtimeOfficeToolCall(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = Field(default=None, max_length=240)
    source: Literal["gpt_realtime"] = "gpt_realtime"


class OfficeTurnRequest(BaseModel):
    conversation_id: str = Field("smart-office-debug", min_length=1, max_length=160)
    visit_id: str | None = Field(default=None, max_length=160)
    text: str = Field(..., max_length=8_000)
    language: Language = "zh"
    input_source: InputSource = "text"
    actor_context: dict[str, Any] = Field(default_factory=dict)
    active_task_id: str | None = None
    realtime_tool_call: RealtimeOfficeToolCall


class SystemVolumeRequest(BaseModel):
    """Bounded compatibility request for the original direct volume endpoint."""

    percent: int = Field(..., ge=0, le=100)


class OfficeTurnResponse(BaseModel):
    conversation_id: str
    route: Literal["office_direct", "office_planned_task"]
    normalized_text: str
    spoken_text: str
    response_language: Language
    task_id: str | None = None
    task_status: str | None = None
    approval_required: bool = False
    actor_type: ActorType
    scene: Literal["office"] = "office"
    permission_decision: Literal["allowed", "denied"]
    content_url: str | None = None
    route_reason: str = ""
    intent_source: str = "gpt_realtime_office_plan"
    realtime_tool_call: RealtimeOfficeToolCall
    tool_result: ToolResult | None = None
    verification_result: VerificationResult | None = None
    presentation_status: dict[str, Any] | None = None
    office_status: dict[str, Any] | None = None
    phase: str = "m3a_fusion_phase_3_gate_3_5"


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


def _actor_type(actor_context: dict[str, Any]) -> ActorType:
    value = str(actor_context.get("type", "visitor")).casefold()
    if value in {"employee", "operator"}:
        return value  # type: ignore[return-value]
    return "visitor"


def _validation_verification(error: ToolResult) -> VerificationResult:
    return VerificationResult(
        ok=False,
        message="Office plan failed Backend validation.",
        process_ok=None,
        window_ok=None,
        expected_process_names=error.expected_process_names,
        expected_window_keywords=error.expected_window_keywords,
        raw={
            "validation_error": error.message,
            "email_send_enabled": False,
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
        },
        checked_at=datetime.now(UTC),
    )


def _presentation_status(status: dict[str, Any]) -> dict[str, Any] | None:
    if "presentation_open" not in status:
        return None
    return {
        key: status.get(key)
        for key in (
            "powerpoint_connected",
            "presentation_open",
            "presentation_name",
            "presentation_path",
            "slideshow_active",
            "current_slide",
            "total_slides",
            "target_monitor_device",
            "slideshow_monitor_device",
            "monitor_placement_enforced",
        )
    }


def _presentation_reply(
    name: str,
    result: ToolResult,
    verification: VerificationResult,
    status: dict[str, Any],
    language: Language,
) -> str:
    if not result.ok:
        return f"PowerPoint 操作没有执行：{result.message}" if language == "zh" else f"The PowerPoint action was not executed: {result.message}"
    if not verification.ok:
        return f"PowerPoint 已收到操作，但实际状态没有通过验证：{verification.message}" if language == "zh" else f"PowerPoint accepted the action, but the observed state did not pass verification: {verification.message}"
    current = status.get("current_slide")
    total = status.get("total_slides")
    monitor = status.get("slideshow_monitor_device") or status.get("target_monitor_device")
    if name == "presentation_get_status":
        if not status.get("presentation_open"):
            return "PowerPoint 当前尚未打开。" if language == "zh" else "PowerPoint is not currently open."
        if status.get("slideshow_active"):
            return f"当前是第 {current} 页，共 {total} 页。" if language == "zh" else f"The slide show is on slide {current} of {total}."
        return f"演示文稿已经打开，共 {total} 页，但尚未开始放映。" if language == "zh" else f"The presentation is open with {total} slides, but the slide show has not started."
    zh = {
        "presentation_open_configured": f"已打开演示文稿 Loss.pptx，共 {total} 页。",
        "presentation_start_slideshow": f"演示已经开始，并已在 {monitor} 放映。当前是第 {current} 页。",
        "presentation_next_slide": f"已翻到下一页。当前是第 {current} 页，共 {total} 页。",
        "presentation_previous_slide": f"已返回上一页。当前是第 {current} 页，共 {total} 页。",
        "presentation_go_to_slide": f"已跳转到第 {current} 页，共 {total} 页。",
        "presentation_end_slideshow": "演示已经结束。",
    }
    en = {
        "presentation_open_configured": f"Loss.pptx is open with {total} slides.",
        "presentation_start_slideshow": f"The slide show has started on {monitor}. It is on slide {current}.",
        "presentation_next_slide": f"Moved to the next slide. This is slide {current} of {total}.",
        "presentation_previous_slide": f"Moved to the previous slide. This is slide {current} of {total}.",
        "presentation_go_to_slide": f"Moved to slide {current} of {total}.",
        "presentation_end_slideshow": "The slide show has ended.",
    }
    return (zh if language == "zh" else en).get(name, result.message)


def _office_reply(
    name: str,
    result: ToolResult,
    verification: VerificationResult,
    status: dict[str, Any],
    language: Language,
) -> str:
    if name.startswith("presentation_"):
        return _presentation_reply(name, result, verification, status, language)
    if not result.ok:
        return f"办公操作没有执行：{result.message}" if language == "zh" else f"The office action was not executed: {result.message}"
    if not verification.ok:
        return f"办公操作已执行，但结果没有通过验证：{verification.message}" if language == "zh" else f"The office action ran, but its result did not pass verification: {verification.message}"
    volume = status.get("volume_percent")
    brightness = status.get("brightness_percent")
    if name == "system_get_status":
        return f"当前系统音量为 {volume if volume is not None else '不可用'}%，屏幕亮度为 {brightness if brightness is not None else '不可用'}%。" if language == "zh" else f"System volume is {volume if volume is not None else 'unavailable'}%, and display brightness is {brightness if brightness is not None else 'unavailable'}%."
    if name in {"system_set_volume", "system_adjust_volume"}:
        return f"系统音量已调整并验证为 {volume}%。" if language == "zh" else f"System volume was adjusted and verified at {volume}%."
    if name in {"system_set_brightness", "system_adjust_brightness"}:
        return f"屏幕亮度已调整并验证为 {brightness}%。" if language == "zh" else f"Display brightness was adjusted and verified at {brightness}%."
    if name == "office_generate_presentation_summary":
        relative = result.data.get("summary_path_relative")
        return f"演示摘要已经生成并验证，文件位于 {relative}。" if language == "zh" else f"The presentation summary was generated and verified at {relative}."
    if name == "outlook_create_summary_draft":
        sender = result.data.get("sender_account_email")
        recipient = result.data.get("recipient_email")
        return f"Outlook 草稿已经创建并验证。发件账号为 {sender}，收件人为 {recipient}。邮件尚未发送。" if language == "zh" else f"The Outlook draft was created and verified from {sender} for {recipient}. It has not been sent."
    if name == "outlook_send_approved_draft":
        sender = result.data.get("sender_account_email")
        recipient = result.data.get("recipient_email")
        return f"第二次批准已执行。草稿提示已删除，Outlook 已接受从 {sender} 发往 {recipient} 的发送操作。" if language == "zh" else f"The second approval was executed. The draft-only notice was removed and Outlook accepted the send from {sender} to {recipient}."
    return result.message


def _expected_visit(req: OfficeTurnRequest) -> str | None:
    return str(req.visit_id or conversation_store.current_visit_id(req.conversation_id) or "").strip() or None


def _assert_visit(conversation_id: str, visit_id: str | None, stage: str) -> None:
    if visit_id and not conversation_store.is_current_visit(conversation_id, visit_id):
        raise HTTPException(status_code=409, detail=f"stale_visit_{stage}")


@router.get("/api/office/status")
def office_status() -> dict:
    status = get_office_status()
    return {
        "ok": status.ok,
        "status": status.data,
        "artifacts": office_artifact_status(),
        "email_send_enabled": False,
        "approval_gated_email_send_enabled": True,
        "unrestricted_email_send_enabled": False,
    }


@router.post("/api/office/system/volume")
async def system_volume(req: SystemVolumeRequest) -> dict[str, Any]:
    """Compatibility URL backed by the current isolated Office worker and verifier."""

    result, verification, status = await asyncio.to_thread(
        execute_office_tool_call,
        "system_set_volume",
        {"value_percent": req.percent},
    )
    return {
        "ok": bool(result.ok and verification.ok),
        "phase": "m3a_fusion_phase_3_gate_3_5",
        "compatibility_endpoint": True,
        "requested_percent": req.percent,
        "result": result.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json"),
        "office_status": status.model_dump(mode="json"),
        "message": verification.message if result.ok else result.message,
    }


@router.post("/agent/office-turn", response_model=OfficeTurnResponse)
async def office_turn(req: OfficeTurnRequest) -> OfficeTurnResponse:
    normalized = _normalise_text(req.text)
    language = _response_language(normalized, req.language)
    actor = _actor_type(req.actor_context)
    conversation_store.get_or_create(req.conversation_id, language=language, actor_type=actor)
    expected_visit_id = _expected_visit(req)
    _assert_visit(req.conversation_id, expected_visit_id, "before_office_turn")

    if req.realtime_tool_call.name != OFFICE_PLAN_TOOL_NAME:
        error = ToolResult(
            tool_name=req.realtime_tool_call.name,
            ok=False,
            message=f"Unsupported office planning function: {req.realtime_tool_call.name}",
            data={"execution_mode": "rejected", "email_send_enabled": False},
        )
        return OfficeTurnResponse(
            conversation_id=req.conversation_id,
            route="office_direct",
            normalized_text=normalized,
            spoken_text=f"办公计划没有执行：{error.message}" if language == "zh" else f"The office plan was not executed: {error.message}",
            response_language=language,
            actor_type=actor,
            permission_decision="allowed" if actor != "visitor" else "denied",
            realtime_tool_call=req.realtime_tool_call,
            tool_result=error,
            verification_result=_validation_verification(error),
            route_reason="Office turn received an unsupported planning function.",
        )

    if actor == "visitor":
        spoken = "该请求属于办公操作。访客不能控制设备或 PowerPoint、生成内部摘要、创建 Outlook 草稿或发送邮件。" if language == "zh" else "Visitors cannot control devices or PowerPoint, generate internal summaries, create Outlook drafts, or send email."
        try:
            conversation_store.update(
                req.conversation_id,
                current_scene="office",
                last_visible_answer=spoken,
                last_command=normalized,
                expected_visit_id=expected_visit_id,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return OfficeTurnResponse(
            conversation_id=req.conversation_id,
            route="office_direct",
            normalized_text=normalized,
            spoken_text=spoken,
            response_language=language,
            actor_type=actor,
            permission_decision="denied",
            realtime_tool_call=req.realtime_tool_call,
            route_reason="Actor permission gate denied the office plan.",
        )

    planned_steps, validation_error = validate_office_plan(req.realtime_tool_call.arguments)
    if validation_error is not None or planned_steps is None:
        error = validation_error or ToolResult(tool_name=OFFICE_PLAN_TOOL_NAME, ok=False, message="Office plan is invalid.")
        return OfficeTurnResponse(
            conversation_id=req.conversation_id,
            route="office_direct",
            normalized_text=normalized,
            spoken_text=f"办公计划没有执行：{error.message}" if language == "zh" else f"The office plan was not executed: {error.message}",
            response_language=language,
            actor_type=actor,
            permission_decision="allowed",
            realtime_tool_call=req.realtime_tool_call,
            tool_result=error,
            verification_result=_validation_verification(error),
            route_reason="Backend validation rejected the office plan.",
        )

    approval_required = any(step.requires_confirmation for step in planned_steps)
    if len(planned_steps) == 1 and not approval_required:
        step = planned_steps[0]
        if step.tool_name is None:
            result = ToolResult(tool_name=OFFICE_PLAN_TOOL_NAME, ok=False, message="Office plan has no executable action.", data={"execution_mode": "rejected"})
            verification = _validation_verification(result)
            status = get_office_status()
        else:
            result, verification, status = await asyncio.to_thread(execute_office_tool_call, step.tool_name, step.args)
        _assert_visit(req.conversation_id, expected_visit_id, "after_office_action")
        status_data = dict(status.data)
        action_name = step.tool_name or OFFICE_PLAN_TOOL_NAME
        spoken = _office_reply(action_name, result, verification, status_data, language)
        try:
            conversation_store.update(
                req.conversation_id,
                current_scene="office",
                last_visible_answer=spoken,
                last_command=normalized,
                expected_visit_id=expected_visit_id,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        content_url = result.data.get("artifact_url")
        return OfficeTurnResponse(
            conversation_id=req.conversation_id,
            route="office_direct",
            normalized_text=normalized,
            spoken_text=spoken,
            response_language=language,
            actor_type=actor,
            permission_decision="allowed",
            content_url=str(content_url) if content_url else None,
            realtime_tool_call=req.realtime_tool_call,
            tool_result=result,
            verification_result=verification,
            presentation_status=_presentation_status(status_data),
            office_status=status_data,
            route_reason="One-step office plan executed in an isolated worker and verified.",
        )

    task = create_office_task(normalized, planned_steps)
    _assert_visit(req.conversation_id, expected_visit_id, "before_task_binding")
    try:
        conversation_store.update(
            req.conversation_id,
            current_scene="office",
            active_task_id=task.task_id,
            set_active_task=True,
            last_command=normalized,
            expected_visit_id=expected_visit_id,
        )
    except RuntimeError as exc:
        state_store.update_pending_steps(task.task_id, "cancelled", message="Owning Visit changed before task binding.")
        state_store.set_status(task.task_id, "cancelled", summary="Owning Visit changed before task binding.")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    asyncio.create_task(run_office_task(task.task_id))

    approval_tools = {step.tool_name for step in planned_steps if step.requires_confirmation}
    scheduled = ToolResult(
        tool_name=OFFICE_PLAN_TOOL_NAME,
        ok=True,
        message="Office plan validated and scheduled.",
        data={
            "execution_mode": "isolated_worker_process",
            "task_id": task.task_id,
            "step_count": len(planned_steps),
            "approval_required": approval_required,
            "requested_state": {"office_plan": True},
            "email_send_enabled": False,
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
        },
    )
    if language == "zh":
        spoken = f"正在执行包含 {len(planned_steps)} 个步骤的办公任务。"
        if {"outlook_create_summary_draft", "outlook_send_approved_draft"}.issubset(approval_tools):
            spoken += " 创建草稿前和发送前会分别暂停，需要两次独立批准。"
        elif "outlook_send_approved_draft" in approval_tools:
            spoken += " 发送邮件前会暂停，等待第二次批准。"
        elif "outlook_create_summary_draft" in approval_tools:
            spoken += " 创建 Outlook 草稿前会暂停，等待第一次批准。"
    else:
        spoken = f"Executing an office task with {len(planned_steps)} steps."
        if {"outlook_create_summary_draft", "outlook_send_approved_draft"}.issubset(approval_tools):
            spoken += " It will pause separately before draft creation and before sending, requiring two approvals."
        elif "outlook_send_approved_draft" in approval_tools:
            spoken += " It will pause for the second approval before sending."
        elif "outlook_create_summary_draft" in approval_tools:
            spoken += " It will pause for the first approval before creating the Outlook draft."
    try:
        conversation_store.update(
            req.conversation_id,
            last_visible_answer=spoken,
            expected_visit_id=expected_visit_id,
        )
    except RuntimeError as exc:
        state_store.cancel_tasks_for_visit(
            conversation_id=req.conversation_id,
            visit_id=expected_visit_id,
            reason="Owning Visit changed before task acknowledgement.",
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return OfficeTurnResponse(
        conversation_id=req.conversation_id,
        route="office_planned_task",
        normalized_text=normalized,
        spoken_text=spoken,
        response_language=language,
        task_id=task.task_id,
        task_status=task.status,
        approval_required=approval_required,
        actor_type=actor,
        permission_decision="allowed",
        realtime_tool_call=req.realtime_tool_call,
        tool_result=scheduled,
        route_reason="Validated office plan scheduled and bound to the current Visit.",
    )


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
