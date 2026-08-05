from __future__ import annotations

import os
import re
from typing import Literal

from app.semantic_pending_store import semantic_pending_intents
from app.semantic_route_models import (
    RecentTurn,
    SemanticProfileExtraction,
    SemanticRoute,
    SemanticRouteRequest,
)
from app.turn_router import classify_turn

RoutingArchitecture = Literal["legacy", "hybrid", "unified"]

_ACTION_VERB_ZH = re.compile(
    r"打开|开启|启动|运行|关闭|关掉|退出|播放|停止|调(?:整|到|成|为)?|设置|设为|"
    r"发送|发给|创建|生成|删除|保存|开始|结束|跳到|预约|登记|提交|批准|取消"
)
_ACTION_TARGET_ZH = re.compile(
    r"teams|微软团队|onenote|微软笔记|powerpoint|ppt|幻灯片|演示文稿|"
    r"音量|扬声器|喇叭|音乐|媒体播放器|outlook|邮件|草稿|"
    r"预约|会议日历|预约日历|登记表|联系方式|录音|对话总结|对话记录|结果中心|"
    r"应用|软件|窗口|面板"
    , re.IGNORECASE,
)
_ACTION_VERB_EN = re.compile(
    r"\b(?:open|launch|start|close|quit|exit|play|stop|set|adjust|change|send|create|"
    r"delete|save|record|next|previous|go\s+to|book|schedule|register|submit|approve|cancel)\b",
    re.IGNORECASE,
)
_ACTION_TARGET_EN = re.compile(
    r"\b(?:teams|onenote|powerpoint|ppt|slide(?:show)?|volume|speaker|music|media\s+player|"
    r"outlook|email|draft|meeting|calendar|appointment|registration|contact\s+form|"
    r"recording|transcript|result\s+center|application|app|software|window|panel)\b",
    re.IGNORECASE,
)
_IMPLICIT_ACTION = re.compile(
    r"下一页|上一页|下一张|上一张|停止音乐|开始录音|结束录音|发出去|发送出去|"
    r"\b(?:next\s+slide|previous\s+slide|send\s+it|stop\s+music|start\s+recording)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_ACTION = re.compile(
    r"^(?:请|麻烦|帮我|请你|请您)?\s*(?:打开|关闭|启动|停止|发送|保存|删除|调一下|设置一下)"
    r"(?:它|这个|那个|一下|掉|出去)?[。.!！?？]?$|"
    r"^(?:please\s+)?(?:open|close|start|stop|send|save|delete|adjust)\s+(?:it|this|that)?[.!?]?$",
    re.IGNORECASE,
)

# These patterns deliberately require a first-person business statement, a pain
# statement, or an explicit conversion/recommendation request. Merely asking what
# Sara knows about an industry remains ordinary conversation.
_SALES_ZH = re.compile(
    r"(?:我|我们|本公司|我们公司).{0,24}(?:从事|属于|是一家|负责|职位|工作|痛点|困难|"
    r"问题|想改善|希望改善|感兴趣|需要方案)|"
    r"(?:会议|客户跟进|重复行政|文件版本|项目协作|信息遗漏).{0,12}(?:太多|很慢|麻烦|困难|"
    r"浪费时间|容易遗漏|是个问题)|"
    r"(?:给我|帮我|可以).{0,10}(?:推荐|安排预约|留下联系方式|完整演示)|"
    r"(?:我想|我们想|我希望|我们希望).{0,16}(?:预约|登记|留联系方式|完整演示|了解价格)|"
    r"(?:多少钱|价格|费用|报价|隐私|人脸数据|保存数据|联系方式)"
)
_SALES_EN = re.compile(
    r"\b(?:i|we|our\s+company)\b.{0,80}\b(?:work\s+in|operate\s+in|industry|role|"
    r"responsible|pain\s+point|problem|interested|need|want\s+to\s+improve)\b|"
    r"\b(?:meetings?|customer\s+follow-up|repetitive\s+administration|file\s+versions?|"
    r"project\s+collaboration)\b.{0,60}\b(?:too\s+many|slow|difficult|time-consuming|problem|missing)\b|"
    r"\b(?:recommend|book\s+(?:a\s+)?meeting|leave\s+(?:my\s+)?contact|contact\s+details|"
    r"full\s+demo|pricing|price|cost|quotation|privacy|face\s+data|store\s+data)\b",
    re.IGNORECASE,
)
_COMPLEX_ZH = re.compile(
    r"详细|深入|全面|系统地|逐步|分步骤|对比分析|比较分析|优缺点|利弊|"
    r"制定.{0,8}(?:计划|方案)|研究一下|评估一下|论证一下|风险分析"
)
_COMPLEX_EN = re.compile(
    r"\b(?:in\s+detail|deeply|thoroughly|comprehensively|step\s+by\s+step|"
    r"detailed\s+analysis|comparative\s+analysis|pros\s+and\s+cons|risk\s+analysis)\b",
    re.IGNORECASE,
)

_AFFIRM = {"可以", "好", "好的", "行", "愿意", "没问题", "yes", "sure", "okay", "ok", "go ahead"}
_REJECT = {"不", "不用", "不用了", "不了", "不需要", "算了", "no", "no thanks", "not now"}


def routing_architecture(value: str | None = None) -> RoutingArchitecture:
    clean = str(value if value is not None else os.getenv("SMART_OFFICE_ROUTING_ARCHITECTURE", "hybrid")).strip().casefold()
    if clean not in {"legacy", "hybrid", "unified"}:
        return "hybrid"
    return clean  # type: ignore[return-value]


def has_action_candidate(text: str) -> bool:
    clean = " ".join(str(text or "").strip().split())
    if _IMPLICIT_ACTION.search(clean) or _AMBIGUOUS_ACTION.search(clean):
        return True
    zh = bool(_ACTION_VERB_ZH.search(clean) and _ACTION_TARGET_ZH.search(clean))
    en = bool(_ACTION_VERB_EN.search(clean) and _ACTION_TARGET_EN.search(clean))
    return zh or en


def has_sales_candidate(text: str) -> bool:
    clean = " ".join(str(text or "").strip().split())
    return bool(_SALES_ZH.search(clean) or _SALES_EN.search(clean))


def conversation_complexity(text: str, language: str) -> Literal["simple", "complex"]:
    clean = " ".join(str(text or "").strip().split())
    if language == "zh":
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", clean))
        clause_count = len(re.findall(r"[，；。！？]", clean))
        return "complex" if cjk_count >= 120 or clause_count >= 5 or _COMPLEX_ZH.search(clean) else "simple"
    word_count = len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", clean))
    clause_count = len(re.findall(r"[,;.!?]", clean))
    return "complex" if word_count >= 55 or clause_count >= 5 or _COMPLEX_EN.search(clean) else "simple"


def recent_context_text(turns: list[RecentTurn], current_text: str) -> str:
    current = " ".join(str(current_text or "").strip().split())
    values = list(turns[-10:])
    if values and values[-1].role == "user" and " ".join(values[-1].text.strip().split()) == current:
        values = values[:-1]
    lines = [f"{item.role.upper()}: {item.text}" for item in values if item.text.strip()]
    return "\n".join(lines)


def attach_recent_context(route: SemanticRoute, request: SemanticRouteRequest) -> SemanticRoute:
    if route.action_mode != "answer_only":
        return route
    context = recent_context_text(request.recent_turns, request.text)
    entities = dict(route.entities)
    entities["language"] = request.language
    entities["recent_context"] = context
    return route.model_copy(update={"entities": entities})


def pending_conversion_route(request: SemanticRouteRequest) -> tuple[SemanticRoute | None, str | None]:
    if not request.visit_id:
        return None, None
    pending = semantic_pending_intents.peek(request.conversation_id, request.visit_id)
    if pending is None or pending.intent_type not in {"booking_offer", "contact_offer"}:
        return None, None
    clean = " ".join(request.text.strip().casefold().strip(" ，。！？!?.,").split())
    response = "accept" if clean in _AFFIRM else "reject" if clean in _REJECT else None
    if response is None:
        return None, None
    semantic_pending_intents.consume(request.conversation_id, request.visit_id)
    booking = pending.intent_type == "booking_offer"
    intent = "booking_response" if booking else "contact_response"
    return (
        SemanticRoute(
            primary_intent=intent,
            domain="sales",
            action_mode="delegate",
            confidence=1.0,
            entities={
                "language": request.language,
                "pending_intent": pending.intent_type,
                "conversion_response": response,
            },
            sales_signals=[f"{'booking' if booking else 'contact'}_{response}"],
            risk="business_state" if response == "accept" else "none",
            reason_codes=[f"hybrid_pending_{pending.intent_type}_{response}"],
            profile_extraction=SemanticProfileExtraction(
                booking_intent=("accept" if response == "accept" else "reject") if booking else "none",
                contact_intent=("accept" if response == "accept" else "reject") if not booking else "none",
                brief_affirmation=response == "accept",
                brief_rejection=response == "reject",
                sales_relevant=True,
            ),
            source="pending_intent",
            answer_engine="backend",
        ),
        pending.intent_type,
    )


def hybrid_conversation_route(
    request: SemanticRouteRequest,
    *,
    reason: str = "hybrid_non_action_conversation_fail_open",
) -> SemanticRoute | None:
    if has_action_candidate(request.text):
        return None
    complexity = conversation_complexity(request.text, request.language)
    context = recent_context_text(request.recent_turns, request.text)
    return SemanticRoute(
        primary_intent="general_question",
        domain="general",
        action_mode="answer_only",
        confidence=1.0,
        entities={"language": request.language, "recent_context": context},
        risk="none",
        reason_codes=[reason],
        source="fast_path",
        complexity=complexity,
        answer_engine="terra" if complexity == "complex" else "realtime",
    )


def hybrid_non_action_route(request: SemanticRouteRequest) -> SemanticRoute | None:
    if has_sales_candidate(request.text):
        return None
    return hybrid_conversation_route(request)


def legacy_route(request: SemanticRouteRequest) -> SemanticRoute:
    decision = classify_turn(request.text, request.actor_type)
    context = recent_context_text(request.recent_turns, request.text)
    if decision.route in {"office_direct", "office_planned_task"}:
        return SemanticRoute(
            primary_intent="office_action",
            domain="office",
            action_mode="delegate",
            confidence=1.0,
            entities={"language": request.language},
            risk="low",
            reason_codes=[f"legacy_router:{decision.reason}"],
            source="fast_path",
            complexity="not_applicable",
            answer_engine="office_interpreter",
        )
    complexity = conversation_complexity(request.text, request.language)
    return SemanticRoute(
        primary_intent="general_question",
        domain="general",
        action_mode="answer_only",
        confidence=1.0,
        entities={"language": request.language, "recent_context": context},
        risk="none",
        reason_codes=[f"legacy_router:{decision.reason}"],
        source="fast_path",
        complexity=complexity,
        answer_engine="terra" if complexity == "complex" else "realtime",
    )
