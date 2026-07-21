from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["agent-turn"])

Language = Literal["zh", "en"]
InputSource = Literal["text", "voice", "touch", "keyboard", "hardware"]
TurnRoute = Literal["realtime_direct", "clarification"]


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


class TurnResponse(BaseModel):
    conversation_id: str
    route: TurnRoute
    normalized_text: str
    spoken_text: str
    task_id: str | None = None
    approval_required: bool = False
    phase: str = "m3a_fusion_phase_1"


def _normalise_text(text: str) -> str:
    return " ".join(text.strip().split())


def _direct_reply(text: str, language: Language) -> tuple[TurnRoute, str]:
    lowered = text.casefold()

    if not text or text == "__UNCLEAR__":
        return (
            "clarification",
            "我没有听清，请再说一次。" if language == "zh" else "I did not catch that. Please try again.",
        )

    stop_terms = {"停止", "停下", "别说了", "stop", "stop speaking", "cancel speech"}
    if lowered in stop_terms:
        return (
            "realtime_direct",
            "好的，我已停止语音输出。" if language == "zh" else "Okay, I have stopped speaking.",
        )

    greeting_terms = {
        "你好",
        "您好",
        "hello",
        "hi",
        "good morning",
        "good afternoon",
    }
    if lowered in greeting_terms:
        return (
            "realtime_direct",
            (
                "您好，我是 Smart Office 虚拟接待与办公助手。现在语音底座已经连接。"
                if language == "zh"
                else "Hello. I am the Smart Office virtual host and office assistant. The voice foundation is connected."
            ),
        )

    identity_signals = (
        "你是谁",
        "介绍一下自己",
        "你能做什么",
        "who are you",
        "what can you do",
        "introduce yourself",
    )
    if any(signal in lowered for signal in identity_signals):
        return (
            "realtime_direct",
            (
                "我是 Smart Office Virtual Host & Agent。后续阶段会把企业接待、Teams、PowerPoint 和受控办公任务接入现有任务执行框架。"
                if language == "zh"
                else "I am the Smart Office Virtual Host and Agent. Later phases will connect reception, Teams, PowerPoint, and controlled office tasks to the existing task runtime."
            ),
        )

    return (
        "realtime_direct",
        (
            f"我已识别到您的请求：{text}。当前 Phase 1 先验证语音输入、文本路由和单一语音输出。"
            if language == "zh"
            else f"I understood your request: {text}. Phase 1 currently validates voice input, text routing, and single-provider voice output."
        ),
    )


@router.get("/agent/turn/status")
def turn_status() -> dict:
    return {
        "ok": True,
        "phase": "m3a_fusion_phase_1",
        "routes": ["realtime_direct", "clarification"],
        "task_creation_enabled": False,
    }


@router.post("/agent/turn", response_model=TurnResponse)
def handle_turn(req: TurnRequest) -> TurnResponse:
    normalized_text = _normalise_text(req.text)
    route, spoken_text = _direct_reply(normalized_text, req.language)
    return TurnResponse(
        conversation_id=req.conversation_id,
        route=route,
        normalized_text=normalized_text,
        spoken_text=spoken_text,
    )
