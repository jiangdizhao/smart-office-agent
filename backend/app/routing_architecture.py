from __future__ import annotations

import os
import re
from typing import Literal

from app.semantic_route_models import RecentTurn, SemanticRoute, SemanticRouteRequest
from app.turn_router import classify_turn

RoutingArchitecture = Literal["legacy", "hybrid", "unified"]

_ACTION_ZH = re.compile(
    r"打开|开启|启动|运行|关闭|关掉|退出|播放|停止|调(?:整|到|成|为)?|设置|设为|"
    r"发送|发给|创建|生成|删除|保存|开始录音|结束录音|下一页|上一页|跳到|"
    r"预约|登记|提交|批准|取消|演示|展示"
)
_ACTION_EN = re.compile(
    r"\b(?:open|launch|start|close|quit|exit|play|stop|set|adjust|change|send|create|"
    r"delete|save|record|next|previous|go\s+to|book|schedule|register|submit|approve|"
    r"cancel|demonstrate|show)\b",
    re.IGNORECASE,
)

# These patterns deliberately require a first-person business statement or an
# explicit conversion/sales request. Merely asking about an industry remains a
# normal knowledge question and must never depend on the semantic model.
_SALES_ZH = re.compile(
    r"(?:我|我们|本公司|我们公司).{0,18}(?:从事|属于|是一家|负责|职位|工作|痛点|困难|"
    r"问题|想改善|希望改善|感兴趣|需要方案)|"
    r"(?:给我|帮我|可以).{0,8}(?:推荐|安排预约|留下联系方式)|"
    r"(?:我想|我们想|我希望|我们希望).{0,12}(?:预约|登记|留联系方式|完整演示|了解价格)|"
    r"(?:多少钱|价格|费用|报价|隐私|人脸数据|保存数据|联系方式)"
)
_SALES_EN = re.compile(
    r"\b(?:i|we|our\s+company)\b.{0,60}\b(?:work\s+in|operate\s+in|industry|role|"
    r"responsible|pain\s+point|problem|interested|need|want\s+to\s+improve)\b|"
    r"\b(?:recommend|book\s+(?:a\s+)?meeting|leave\s+(?:my\s+)?contact|contact\s+details|"
    r"pricing|price|cost|quotation|privacy|face\s+data|store\s+data)\b",
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


def routing_architecture(value: str | None = None) -> RoutingArchitecture:
    clean = str(value if value is not None else os.getenv("SMART_OFFICE_ROUTING_ARCHITECTURE", "hybrid")).strip().casefold()
    if clean not in {"legacy", "hybrid", "unified"}:
        return "hybrid"
    return clean  # type: ignore[return-value]


def has_action_candidate(text: str) -> bool:
    clean = " ".join(str(text or "").strip().split())
    return bool(_ACTION_ZH.search(clean) or _ACTION_EN.search(clean))


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


def hybrid_non_action_route(request: SemanticRouteRequest) -> SemanticRoute | None:
    if has_action_candidate(request.text) or has_sales_candidate(request.text):
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
        reason_codes=["hybrid_non_action_conversation_fail_open"],
        source="fast_path",
        complexity=complexity,
        answer_engine="terra" if complexity == "complex" else "realtime",
    )


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
