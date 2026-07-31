from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.conversation_store import ActorType, conversation_store
from app.openai_services import generate_response_text
from app.turn_api import TurnRequest, handle_turn
from app.turn_router import classify_turn

router = APIRouter(tags=["general-chat"])

Language = Literal["zh", "en"]


class GeneralChatRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=160)
    text: str = Field(..., min_length=1, max_length=12_000)
    language: Language = "zh"
    actor_type: ActorType = "visitor"


class GeneralChatResponse(BaseModel):
    ok: bool = True
    route: str
    spoken_text: str
    response_language: Language
    model: str
    permission_decision: str = "not_required"
    content_url: str | None = None


def _history_text(context: dict[str, Any], current_text: str) -> str:
    messages = context.get("recent_messages")
    if not isinstance(messages, list):
        return "(none)"
    lines: list[str] = []
    for item in messages[-12:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"{role.upper()}: {text}")
    if lines and lines[-1].endswith(current_text.strip()):
        lines = lines[:-1]
    return "\n".join(lines) if lines else "(none)"


def _instructions(language: Language) -> str:
    if language == "en":
        return """
You are the general conversational intelligence of a Smart Office virtual host.
Answer ordinary questions across general knowledge, science, technology, daily life, education, travel, culture, and other legitimate topics. Do not limit yourself to company information or self-introduction.
Use the conversation history when it is relevant. Give a direct, useful, naturally spoken answer in English.
When the recent assistant message introduced Sara and asked whether the visitor would like a quick demonstration, treat a brief affirmative reply such as yes, sure, okay, or why not as acceptance. Acknowledge it warmly and ask the visitor to choose PowerPoint voice control, Outlook assistance, or a general question.
When that invitation is followed by a clear refusal such as no, no thanks, not now, or maybe later, give a brief polite farewell without pressure and do not ask another question.
If the visitor directly requests a supported Office action instead of saying yes, respond naturally and let the deterministic Office router handle the action.
Do not claim that you executed an Office action, changed a device, sent email, opened software, or created a file. Those actions are handled by separate deterministic tools.
Keep the answer suitable for speech unless the user explicitly requests a detailed explanation.
Follow normal safety requirements and state uncertainty when necessary.
Return only the final answer, without labels, JSON, or Markdown fences.
""".strip()
    return """
你是 Smart Office 虚拟接待员的通用对话智能层。
用户可以询问一般知识、科学技术、日常生活、教育、旅行、文化及其他正当话题。回答范围不得局限于公司业务或自我介绍。
在相关时使用最近对话上下文，以自然、直接、适合朗读的中文回答；用户明确要求详细解释时可以展开。
如果最近一条助手消息刚刚介绍了 Sara，并询问访客是否愿意体验快速演示，那么“可以”“好”“愿意”“行”“试一下”等简短肯定回答表示接受。应亲切确认，并请访客从 PowerPoint 语音控制、Outlook 助手或一般问题中选择一项。
如果访客明确回答“不用了”“不了”“不需要”“暂时不用”或类似拒绝，应礼貌、简短地结束，不施压，也不要继续追问。
如果访客没有先回答“愿意”，而是直接提出支持的 Office 操作，应自然衔接，并交给确定性 Office 路由执行。
不得声称已经执行 Office 操作、修改设备、发送邮件、打开软件或创建文件；这些动作由独立的确定性工具完成。
遵守正常安全要求，无法确定时明确说明不确定性。
只输出最终答复，不要输出标签、JSON 或 Markdown 代码围栏。
""".strip()


async def generate_general_chat_answer(
    *,
    conversation_id: str,
    text: str,
    language: Language,
    actor_type: ActorType,
) -> tuple[str, str]:
    clean = " ".join(text.strip().split())
    context = conversation_store.context_snapshot(
        conversation_id,
        language=language,
        actor_type=actor_type,
    )
    history = _history_text(context, clean)
    input_text = (
        f"Recent conversation:\n{history}\n\nCurrent user message:\n{clean}"
        if language == "en"
        else f"最近对话：\n{history}\n\n当前用户问题：\n{clean}"
    )
    return await generate_response_text(
        input_text=input_text,
        instructions=_instructions(language),
        max_output_tokens=1600,
    )


@router.post("/api/general-chat", response_model=GeneralChatResponse)
async def general_chat(req: GeneralChatRequest) -> GeneralChatResponse:
    clean = " ".join(req.text.strip().split())
    context = conversation_store.context_snapshot(
        req.conversation_id,
        language=req.language,
        actor_type=req.actor_type,
    )
    decision = classify_turn(clean, req.actor_type)

    if decision.reason != "general_direct_conversation":
        delegated = await handle_turn(
            TurnRequest(
                conversation_id=req.conversation_id,
                text=clean,
                language=req.language,
                input_source="voice",
                actor_context={"type": req.actor_type, "source": "general_chat_router_delegate"},
                active_task_id=context.get("active_task_id"),
                realtime_tool_call=None,
            )
        )
        return GeneralChatResponse(
            route=delegated.route,
            spoken_text=delegated.spoken_text,
            response_language=delegated.response_language,
            model="deterministic_turn_router",
            permission_decision=delegated.permission_decision,
            content_url=delegated.content_url,
        )

    try:
        answer, model = await generate_general_chat_answer(
            conversation_id=req.conversation_id,
            text=clean,
            language=req.language,
            actor_type=req.actor_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return GeneralChatResponse(
        route="general_chat",
        spoken_text=answer,
        response_language=req.language,
        model=model,
    )
