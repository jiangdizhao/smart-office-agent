from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.conversation_store import ActorType, conversation_store
from app.openai_services import generate_response_text
from app.turn_api import TurnRequest, handle_turn
from app.turn_router import classify_turn

router = APIRouter(tags=["general-chat"])

Language = Literal["zh", "en"]
ConversationComplexity = Literal["simple", "complex", "not_applicable"]
AnswerEngine = Literal["realtime", "terra", "office_interpreter", "backend"]

_COMPLEX_ZH_PATTERN = re.compile(
    r"详细(?:解释|说明|分析)|深入(?:解释|说明|分析|讨论)|全面(?:解释|说明|分析|比较)|"
    r"系统地(?:解释|说明|分析)|逐步(?:解释|说明|分析)|分步骤(?:解释|说明)|"
    r"对比分析|比较分析|优缺点|利弊|制定(?:一个|一份)?(?:详细)?(?:计划|方案)|"
    r"研究一下|评估一下|论证一下|法律建议|医疗建议|财务建议|投资建议|风险分析"
)
_COMPLEX_EN_PATTERN = re.compile(
    r"\b(?:explain|describe|analyse|analyze|compare|evaluate|assess|research|discuss)\b.{0,24}"
    r"\b(?:in detail|deeply|thoroughly|comprehensively|step by step)\b|"
    r"\b(?:detailed analysis|comparative analysis|pros and cons|advantages and disadvantages|"
    r"legal advice|medical advice|financial advice|investment advice|risk analysis)\b",
    re.IGNORECASE,
)


class ConversationRouteRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=160)
    text: str = Field(..., min_length=1, max_length=12_000)
    language: Language = "zh"
    actor_type: ActorType = "visitor"
    visit_id: str | None = Field(default=None, max_length=160)


class ConversationRouteResponse(BaseModel):
    ok: bool = True
    route: str
    scene: str
    route_reason: str
    conversation_complexity: ConversationComplexity
    answer_engine: AnswerEngine
    recent_context: str
    visit_id: str | None = None


class GeneralChatRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=160)
    text: str = Field(..., min_length=1, max_length=12_000)
    language: Language = "zh"
    actor_type: ActorType = "visitor"
    visit_id: str | None = Field(default=None, max_length=160)


class GeneralChatResponse(BaseModel):
    ok: bool = True
    route: str
    spoken_text: str
    response_language: Language
    model: str
    permission_decision: str = "not_required"
    content_url: str | None = None
    visit_id: str | None = None


def _history_text(context: dict[str, Any], current_text: str, language: Language) -> str:
    lines: list[str] = []
    registered_memory = " ".join(
        str(context.get("registered_memory_summary") or "").strip().split()
    )
    if registered_memory:
        label = "REGISTERED VISITOR MEMORY" if language == "en" else "注册访客长期记忆"
        lines.append(f"{label}: {registered_memory}")

    messages = context.get("recent_messages")
    if isinstance(messages, list):
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


def _conversation_complexity(text: str, language: Language) -> ConversationComplexity:
    clean = " ".join(text.strip().split())
    if language == "zh":
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", clean))
        clause_count = len(re.findall(r"[，；。！？]", clean))
        if cjk_count >= 120 or clause_count >= 5 or _COMPLEX_ZH_PATTERN.search(clean):
            return "complex"
        return "simple"

    word_count = len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", clean))
    clause_count = len(re.findall(r"[,;.!?]", clean))
    if word_count >= 55 or clause_count >= 5 or _COMPLEX_EN_PATTERN.search(clean):
        return "complex"
    return "simple"


def _answer_engine(route: str, reason: str, complexity: ConversationComplexity) -> AnswerEngine:
    if route in {"office_direct", "office_planned_task"}:
        return "office_interpreter"
    if route == "realtime_direct" and reason == "general_direct_conversation":
        return "terra" if complexity == "complex" else "realtime"
    return "backend"


def _instructions(language: Language) -> str:
    if language == "en":
        return """
You are the general conversational intelligence of a Smart Office virtual host.
Answer ordinary questions across general knowledge, science, technology, daily life, education, travel, culture, and other legitimate topics. Do not limit yourself to company information or self-introduction.
Use the conversation history when it is relevant. A line labelled REGISTERED VISITOR MEMORY is a trusted summary from an earlier visit by the same consented registered identity. Use it only when useful and do not reveal that it came from biometric recognition unless the visitor asks.
Give a direct, useful, naturally spoken answer in English.
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
在相关时使用最近对话上下文。标记为“注册访客长期记忆”的内容，是同一个经过同意的注册身份在上一次到访中留下的摘要；仅在有帮助时自然使用，不要主动透露这是通过人脸身份识别加载的。
以自然、直接、适合朗读的中文回答；用户明确要求详细解释时可以展开。
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
    history = _history_text(context, clean, language)
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


@router.post("/api/conversation-route", response_model=ConversationRouteResponse)
async def conversation_route(req: ConversationRouteRequest) -> ConversationRouteResponse:
    clean = " ".join(req.text.strip().split())
    context = conversation_store.context_snapshot(
        req.conversation_id,
        language=req.language,
        actor_type=req.actor_type,
    )
    expected_visit_id = str(req.visit_id or context.get("visit_id") or "").strip() or None
    if req.visit_id and not conversation_store.is_current_visit(req.conversation_id, req.visit_id):
        raise HTTPException(status_code=409, detail="stale_visit_before_route_preview")

    decision = classify_turn(clean, req.actor_type)
    complexity: ConversationComplexity = (
        _conversation_complexity(clean, req.language)
        if decision.reason == "general_direct_conversation"
        else "not_applicable"
    )
    return ConversationRouteResponse(
        route=decision.route,
        scene=decision.scene,
        route_reason=decision.reason,
        conversation_complexity=complexity,
        answer_engine=_answer_engine(decision.route, decision.reason, complexity),
        recent_context=_history_text(context, clean, req.language),
        visit_id=expected_visit_id,
    )


@router.post("/api/general-chat", response_model=GeneralChatResponse)
async def general_chat(req: GeneralChatRequest) -> GeneralChatResponse:
    clean = " ".join(req.text.strip().split())
    context = conversation_store.context_snapshot(
        req.conversation_id,
        language=req.language,
        actor_type=req.actor_type,
    )
    expected_visit_id = str(req.visit_id or context.get("visit_id") or "").strip() or None
    if req.visit_id and not conversation_store.is_current_visit(req.conversation_id, req.visit_id):
        raise HTTPException(status_code=409, detail="stale_visit_before_general_chat")

    decision = classify_turn(clean, req.actor_type)
    if decision.reason != "general_direct_conversation":
        delegated = await handle_turn(
            TurnRequest(
                conversation_id=req.conversation_id,
                visit_id=expected_visit_id,
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
            visit_id=expected_visit_id,
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

    if expected_visit_id and not conversation_store.is_current_visit(
        req.conversation_id,
        expected_visit_id,
    ):
        raise HTTPException(status_code=409, detail="stale_visit_after_general_chat")

    return GeneralChatResponse(
        route="general_chat",
        spoken_text=answer,
        response_language=req.language,
        model=model,
        visit_id=expected_visit_id,
    )
