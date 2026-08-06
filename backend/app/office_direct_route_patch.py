from __future__ import annotations

import re
from typing import Any

from app import semantic_route_api
from app.semantic_route_models import SemanticAction, SemanticRoute, SemanticRouteRequest

_original_classify_with_layers = semantic_route_api._classify_with_layers

_PPT = re.compile(
    r"ppt|power\s*point|powerpoint|幻灯片|演示文稿|presentation|"
    r"\bslides?\b|slide\s*show|slideshow",
    re.IGNORECASE,
)
_PPT_CONTEXT = re.compile(
    r"下一页|下一张|后一页|后一张|往后|向后|继续(?:播放|演示|放映|翻)?|"
    r"上一页|上一张|前一页|前一张|往前|向前|返回|翻回去|"
    r"最后一页|末页|第\s*[一二两三四五六七八九十百\d]+\s*[页张]|"
    r"next(?:\s+slide)?|previous(?:\s+slide)?|go\s+back|continue|last\s+slide",
    re.IGNORECASE,
)
_OFFICE_ENTITY = re.compile(
    r"teams|one\s*note|onenote|outlook|word|excel|邮件|邮箱|草稿|"
    r"音量|系统声音|电脑声音|亮度|音乐|媒体播放器|录音|结果中心|"
    r"office|volume|brightness|music|recording|result\s*center",
    re.IGNORECASE,
)
_OFFICE_ACTION = re.compile(
    r"打开|关闭|启动|停止|退出|创建|生成|发送|调整|设置|播放|执行|"
    r"总结|整理|查看|读取|操作|调到|调成|提高|降低|增大|减小|"
    r"open|close|launch|start|stop|exit|create|generate|send|adjust|set|"
    r"play|execute|summari[sz]e|review|read|operate|increase|decrease",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"不要|别|无需|不用|请勿|do\s+not|don't|without", re.IGNORECASE)


def _action(
    verb: str,
    target: str,
    evidence: str,
    *,
    arguments: dict[str, Any] | None = None,
    sequence: int = 1,
) -> SemanticAction:
    return SemanticAction(
        verb=verb,  # type: ignore[arg-type]
        target=target,
        arguments=dict(arguments or {}),
        polarity="affirmed",
        speech_act="command",
        evidence=evidence,
        sequence=sequence,
    )


def _slide_number(text: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*[页张]|(?:go|jump|move)\s+to\s+(?:slide\s+)?(\d+)", text, re.I)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _ppt_actions(text: str) -> list[SemanticAction]:
    evidence = text
    if re.search(r"下一页|下一张|后一页|后一张|往后|向后|继续|next", text, re.I):
        return [_action("next", "powerpoint", evidence)]
    if re.search(r"上一页|上一张|前一页|前一张|往前|向前|返回|翻回去|previous|go\s+back", text, re.I):
        return [_action("previous", "powerpoint", evidence)]
    if re.search(r"最后一页|末页|last\s+slide|final\s+slide", text, re.I):
        return [_action("goto", "powerpoint", evidence, arguments={"slide_target": "last"})]
    number = _slide_number(text)
    if number is not None:
        return [_action("goto", "powerpoint", evidence, arguments={"slide_number": number})]
    if re.search(r"结束|停止|退出放映|end|stop|exit", text, re.I) and re.search(r"演示|放映|ppt|powerpoint|slideshow|presentation", text, re.I):
        return [_action("stop", "powerpoint", evidence)]
    if re.search(r"关闭|关掉|close|quit", text, re.I):
        return [_action("close", "powerpoint", evidence)]
    if re.search(r"状态|第几页|多少页|current\s+slide|status", text, re.I):
        return [_action("show", "powerpoint", evidence, arguments={"status": True})]
    if re.search(r"开始|播放|演示|展示|放映|全屏|present|show|play|run|start", text, re.I):
        return [_action("start", "powerpoint", evidence)]
    if re.search(r"打开|开启|启动|调出|弄出|open|launch", text, re.I):
        return [_action("open", "powerpoint", evidence)]

    # Exhibition rule: any affirmative PPT mention is a command. The default
    # visible action is to open the configured deck and start its slide show.
    return [
        _action("open", "powerpoint", evidence, sequence=1),
        _action("start", "powerpoint", evidence, sequence=2),
    ]


def _office_target(text: str) -> str:
    mappings = (
        (r"teams|微软团队", "teams"),
        (r"one\s*note|onenote|微软笔记", "onenote"),
        (r"outlook|邮件|邮箱|草稿", "outlook"),
        (r"音量|系统声音|电脑声音|volume", "system_volume"),
        (r"亮度|brightness", "system_brightness"),
        (r"音乐|媒体播放器|music", "music"),
        (r"录音|recording", "recording"),
        (r"结果中心|result\s*center", "result_center"),
        (r"word", "word"),
        (r"excel", "excel"),
    )
    for pattern, target in mappings:
        if re.search(pattern, text, re.I):
            return target
    return "office"


def _office_verb(text: str) -> str:
    if re.search(r"关闭|停止|退出|close|stop|exit|quit", text, re.I):
        return "stop"
    if re.search(r"发送|send", text, re.I):
        return "send"
    if re.search(r"创建|生成|create|generate", text, re.I):
        return "create"
    if re.search(r"总结|整理|summari[sz]e", text, re.I):
        return "summarise"
    if re.search(r"设置|调整|调到|调成|提高|降低|set|adjust|increase|decrease", text, re.I):
        return "set"
    if re.search(r"播放|play", text, re.I):
        return "start"
    return "open"


def _forced_route(request: SemanticRouteRequest) -> SemanticRoute | None:
    text = request.text.strip()
    if not text or _NEGATED.search(text):
        return None

    if _PPT.search(text) or _PPT_CONTEXT.search(text):
        actions = _ppt_actions(text)
        return SemanticRoute(
            primary_intent=("multi_action_workflow" if len(actions) > 1 else "presentation_action"),
            domain="office",
            action_mode="execute",
            confidence=1.0,
            actions=actions,
            entities={"language": request.language, "office_direct": True},
            risk="low",
            reason_codes=["exhibition_unconditional_presentation_execution"],
            source="fast_path",
            complexity="not_applicable",
            answer_engine="office_interpreter",
        )

    if _OFFICE_ENTITY.search(text) and _OFFICE_ACTION.search(text):
        target = _office_target(text)
        return SemanticRoute(
            primary_intent="office_action",
            domain="office",
            action_mode="execute",
            confidence=1.0,
            actions=[_action(_office_verb(text), target, text)],
            entities={"language": request.language, "office_direct": True},
            risk="low",
            reason_codes=["exhibition_unconditional_office_execution"],
            source="fast_path",
            complexity="not_applicable",
            answer_engine="office_interpreter",
        )
    return None


async def _patched_classify_with_layers(request: SemanticRouteRequest):
    forced = _forced_route(request)
    if forced is not None:
        return forced, None, None, "office_direct"
    return await _original_classify_with_layers(request)


semantic_route_api._classify_with_layers = _patched_classify_with_layers
