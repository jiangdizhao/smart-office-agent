from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.conversation_store import ActorType

TurnRoute = Literal[
    "realtime_direct",
    "reception_knowledge",
    "office_direct",
    "office_planned_task",
    "approval_action",
    "clarification",
]
ApprovalAction = Literal["approve", "cancel", "skip", "takeover"]


@dataclass(frozen=True)
class RouteDecision:
    route: TurnRoute
    scene: Literal["reception", "office", "meeting"]
    approval_action: ApprovalAction | None = None
    reason: str = ""


_GREETING_TERMS = {
    "你好",
    "您好",
    "hello",
    "hi",
    "good morning",
    "good afternoon",
}
_STOP_TERMS = {
    "停止",
    "停下",
    "别说了",
    "stop",
    "stop speaking",
    "cancel speech",
}
_REPEAT_TERMS = {
    "重复",
    "再说一遍",
    "请重复",
    "repeat",
    "say that again",
}
_APPROVAL_TERMS: dict[ApprovalAction, set[str]] = {
    "approve": {"同意", "批准", "确认", "继续", "approve", "approved", "confirm", "continue"},
    "cancel": {"取消任务", "取消这个任务", "终止任务", "cancel task", "cancel the task"},
    "skip": {"跳过", "跳过这一步", "skip", "skip this step"},
    "takeover": {"人工接管", "我来操作", "take over", "manual takeover"},
}
_RECEPTION_TERMS = (
    "公司",
    "企业",
    "业务",
    "产品",
    "方案",
    "解决方案",
    "服务",
    "案例",
    "价格",
    "报价",
    "联系方式",
    "联系",
    "预约",
    "参观",
    "招聘",
    "供应商",
    "你是谁",
    "介绍一下自己",
    "你能做什么",
    "company",
    "business",
    "product",
    "solution",
    "service",
    "case study",
    "pricing",
    "contact",
    "appointment",
    "visitor",
    "who are you",
    "what can you do",
    "introduce yourself",
)
_OFFICE_ENTITY_TERMS = (
    "teams",
    "powerpoint",
    "ppt",
    "word",
    "excel",
    "outlook",
    "onenote",
    "会议",
    "幻灯片",
    "下一页",
    "上一页",
    "静音",
    "麦克风",
    "摄像头",
    "共享屏幕",
    "生成文档",
    "邮件",
    "meeting",
    "presentation",
    "slide",
    "next slide",
    "previous slide",
    "mute",
    "microphone",
    "camera",
    "screen sharing",
    "document",
    "email",
)
_COMPLEX_OFFICE_TERMS = (
    "准备",
    "安排",
    "总结",
    "生成",
    "整理",
    "分析",
    "创建",
    "并且",
    "然后",
    "同时",
    "之后",
    "prepare",
    "schedule",
    "summarize",
    "generate",
    "analyse",
    "analyze",
    "create",
    "and then",
    "after that",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _approval_action(text: str) -> ApprovalAction | None:
    for action, terms in _APPROVAL_TERMS.items():
        if text in terms:
            return action
    return None


def classify_turn(text: str, actor_type: ActorType) -> RouteDecision:
    normalized = text.strip()
    lowered = normalized.casefold()

    if not normalized or normalized == "__UNCLEAR__":
        return RouteDecision("clarification", "reception", reason="empty_or_unclear")

    approval = _approval_action(lowered)
    if approval is not None:
        return RouteDecision(
            "approval_action",
            "office",
            approval_action=approval,
            reason="explicit_approval_action",
        )

    if lowered in _GREETING_TERMS or lowered in _STOP_TERMS or lowered in _REPEAT_TERMS:
        return RouteDecision("realtime_direct", "reception", reason="direct_control_or_greeting")

    reception_match = _contains_any(lowered, _RECEPTION_TERMS)
    office_match = _contains_any(lowered, _OFFICE_ENTITY_TERMS)

    # Reception content remains a reception request even when the user asks to
    # "open" or "show" it. Workspace execution is introduced in Phase 3.
    if reception_match and not office_match:
        return RouteDecision("reception_knowledge", "reception", reason="reception_intent")

    if office_match:
        complex_goal = _contains_any(lowered, _COMPLEX_OFFICE_TERMS)
        multiple_office_entities = sum(term in lowered for term in _OFFICE_ENTITY_TERMS) >= 2
        route: TurnRoute = (
            "office_planned_task" if complex_goal or multiple_office_entities else "office_direct"
        )
        scene = "meeting" if any(term in lowered for term in ("teams", "会议", "meeting")) else "office"
        return RouteDecision(route, scene, reason=f"office_intent:{actor_type}")

    if reception_match:
        return RouteDecision("reception_knowledge", "reception", reason="reception_intent")

    return RouteDecision("realtime_direct", "reception", reason="general_direct_conversation")
