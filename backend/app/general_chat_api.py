from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.conversation_store import ActorType, conversation_store
from app.openai_services import generate_response_text

router = APIRouter(tags=["general-chat"])

Language = Literal["zh", "en"]


class GeneralChatRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=160)
    text: str = Field(..., min_length=1, max_length=12_000)
    language: Language = "zh"
    actor_type: ActorType = "visitor"


class GeneralChatResponse(BaseModel):
    ok: bool = True
    route: Literal["general_chat"] = "general_chat"
    spoken_text: str
    response_language: Language
    model: str
    permission_decision: Literal["not_required"] = "not_required"
    content_url: None = None


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
Do not claim that you executed an Office action, changed a device, sent email, opened software, or created a file. Those actions are handled by separate deterministic tools.
Keep the answer suitable for speech unless the user explicitly requests a detailed explanation.
Follow normal safety requirements and state uncertainty when necessary.
Return only the final answer, without labels, JSON, or Markdown fences.
""".strip()
    return """
你是 Smart Office 虚拟接待员的通用对话智能层。
用户可以询问一般知识、科学技术、日常生活、教育、旅行、文化及其他正当话题。回答范围不得局限于公司业务或自我介绍。
在相关时使用最近对话上下文，以自然、直接、适合朗读的中文回答；用户明确要求详细解释时可以展开。
不得声称已经执行 Office 操作、修改设备、发送邮件、打开软件或创建文件；这些动作由独立的确定性工具完成。
遵守正常安全要求，无法确定时明确说明不确定性。
只输出最终答复，不要输出标签、JSON 或 Markdown 代码围栏。
""".strip()


@router.post("/api/general-chat", response_model=GeneralChatResponse)
async def general_chat(req: GeneralChatRequest) -> GeneralChatResponse:
    clean = " ".join(req.text.strip().split())
    context = conversation_store.context_snapshot(
        req.conversation_id,
        language=req.language,
        actor_type=req.actor_type,
    )
    history = _history_text(context, clean)
    input_text = (
        f"Recent conversation:\n{history}\n\nCurrent user message:\n{clean}"
        if req.language == "en"
        else f"最近对话：\n{history}\n\n当前用户问题：\n{clean}"
    )
    try:
        answer, model = await generate_response_text(
            input_text=input_text,
            instructions=_instructions(req.language),
            max_output_tokens=1600,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return GeneralChatResponse(
        spoken_text=answer,
        response_language=req.language,
        model=model,
    )
