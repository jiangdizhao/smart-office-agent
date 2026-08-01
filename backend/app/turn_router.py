from __future__ import annotations

import re
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
    "演示文稿",
    "下一页",
    "上一页",
    "下一张",
    "上一张",
    "后一页",
    "前一页",
    "后一张",
    "前一张",
    "静音",
    "取消静音",
    "麦克风",
    "摄像头",
    "共享屏幕",
    "生成文档",
    "邮件",
    "meeting",
    "presentation",
    "slide",
    "slideshow",
    "next slide",
    "previous slide",
    "mute",
    "unmute",
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

_INTENT_SEPARATOR_PATTERN = re.compile(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()\-]+")
_SELF_INTRO_MARKERS = (
    "介绍一下你自己",
    "介绍下你自己",
    "介绍你自己",
    "介绍一下自己",
    "介绍下自己",
    "介绍一下您自己",
    "介绍您自己",
    "你是谁",
    "您是谁",
    "你能做什么",
    "您能做什么",
    "你可以做什么",
    "您可以做什么",
    "你会做什么",
    "introduceyourself",
    "tellmeaboutyourself",
    "whoareyou",
    "whatcanyoudo",
)

_VOLUME_INTENT_PATTERN = re.compile(
    r"(?:系统|电脑|扬声器|播放)?(?:音量|声音).{0,10}"
    r"(?:调|设|改|升|降|大|小|高|低|静音|取消静音|多少|几|百分之|\d+)"
    r"|(?:调|设|改|提高|降低|增大|减小|打开|关闭|取消).{0,8}"
    r"(?:系统|电脑|扬声器|播放)?(?:音量|声音|静音)"
    r"|\b(?:set|adjust|change|increase|decrease|raise|lower|turn\s+up|turn\s+down|mute|unmute)\b"
    r".{0,20}\b(?:volume|sound|audio)\b"
    r"|\b(?:volume|sound|audio)\b.{0,20}"
    r"\b(?:up|down|higher|lower|louder|quieter|mute|unmute|percent|\d+)\b",
    re.IGNORECASE,
)
_PRESENTATION_ACTION_PATTERN = re.compile(
    r"(?:开始|启动|进入|播放|继续|停止|结束|退出|关闭|全屏).{0,6}"
    r"(?:演示|放映|幻灯片|演示文稿)"
    r"|(?:演示|放映|播放|展示).{0,6}(?:ppt|powerpoint|幻灯片|演示文稿)"
    r"|(?:ppt|powerpoint|幻灯片|演示文稿).{0,6}"
    r"(?:演示|放映|播放|展示|全屏)"
    r"|\b(?:start|begin|play|run|continue|stop|end|exit)\b.{0,20}"
    r"\b(?:presentation|slide\s*show|slideshow)\b"
    r"|\b(?:present|show)\b.{0,12}\b(?:ppt|powerpoint|presentation|slides?)\b",
    re.IGNORECASE,
)
_CONTEXTUAL_PRESENTATION_TERMS = {
    "开始演示",
    "开始放映",
    "继续演示",
    "继续放映",
    "播放这个",
    "把它放映出来",
    "结束演示",
    "结束放映",
    "退出演示",
    "退出放映",
    "退出全屏",
    "下一张",
    "上一张",
    "后一张",
    "前一张",
    "往后翻",
    "往前翻",
    "start the show",
    "start presenting",
    "continue presenting",
    "end the show",
    "exit the slideshow",
}


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _normalise_office_text(text: str) -> str:
    normalized = text.casefold()
    normalized = re.sub(r"\bp\s*[.\-_]?\s*p\s*[.\-_]?\s*t\b", "ppt", normalized)
    normalized = re.sub(r"\bpower\s+point\b", "powerpoint", normalized)
    normalized = re.sub(r"幻\s*灯\s*片", "幻灯片", normalized)
    return " ".join(normalized.split())


def _compact_intent_text(text: str) -> str:
    return _INTENT_SEPARATOR_PATTERN.sub("", text.casefold())


def _is_self_introduction(text: str) -> bool:
    compact = _compact_intent_text(text)
    return any(marker in compact for marker in _SELF_INTRO_MARKERS)


def _is_reception_intent(text: str) -> bool:
    return _is_self_introduction(text) or _contains_any(text, _RECEPTION_TERMS)


def _approval_action(text: str) -> ApprovalAction | None:
    for action, terms in _APPROVAL_TERMS.items():
        if text in terms:
            return action
    return None


def _is_volume_intent(text: str) -> bool:
    return bool(_VOLUME_INTENT_PATTERN.search(text))


def _is_presentation_action(text: str, *, office_context_active: bool) -> bool:
    if _PRESENTATION_ACTION_PATTERN.search(text):
        return True
    compact = _compact_intent_text(text)
    if compact in {_compact_intent_text(item) for item in _CONTEXTUAL_PRESENTATION_TERMS}:
        return office_context_active or any(
            marker in compact for marker in ("演示", "放映", "slideshow", "present")
        )
    return False


def _is_compound_office_request(text: str) -> bool:
    return bool(
        ("打开" in text and any(term in text for term in ("演示", "放映", "播放")))
        or (
            any(term in text for term in ("open", "launch"))
            and any(term in text for term in ("present", "slideshow", "play"))
        )
    )


def classify_turn(
    text: str,
    actor_type: ActorType,
    *,
    office_context_active: bool = False,
) -> RouteDecision:
    normalized = text.strip()
    lowered = _normalise_office_text(normalized)

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

    reception_match = _is_reception_intent(lowered)
    volume_match = _is_volume_intent(lowered)
    presentation_action_match = _is_presentation_action(
        lowered,
        office_context_active=office_context_active,
    )
    office_match = (
        _contains_any(lowered, _OFFICE_ENTITY_TERMS)
        or volume_match
        or presentation_action_match
    )

    # Reception content remains a reception request even when the user asks to
    # "open" or "show" it, unless the same utterance clearly names an Office
    # entity, system volume action, or PowerPoint slide-show action.
    if reception_match and not office_match:
        return RouteDecision("reception_knowledge", "reception", reason="reception_intent")

    if office_match:
        complex_goal = (
            _contains_any(lowered, _COMPLEX_OFFICE_TERMS)
            or _is_compound_office_request(lowered)
        )
        multiple_office_entities = sum(term in lowered for term in _OFFICE_ENTITY_TERMS) >= 2
        route: TurnRoute = (
            "office_planned_task" if complex_goal or multiple_office_entities else "office_direct"
        )
        scene = "meeting" if any(term in lowered for term in ("teams", "会议", "meeting")) else "office"
        reason_kind = (
            "volume_intent"
            if volume_match
            else "presentation_action"
            if presentation_action_match
            else "office_entity"
        )
        return RouteDecision(
            route,
            scene,
            reason=f"office_intent:{reason_kind}:{actor_type}",
        )

    if reception_match:
        return RouteDecision("reception_knowledge", "reception", reason="reception_intent")

    return RouteDecision("realtime_direct", "reception", reason="general_direct_conversation")
