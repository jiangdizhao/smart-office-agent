from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app import contact_record_api
from app.conversation_store import conversation_store

_original_upsert_summary = contact_record_api._upsert_summary

_GREETING_ONLY = re.compile(
    r"^(?:你好|您好|嗨|谢谢|多谢|好的?|可以|行|嗯|哦|再见|"
    r"hello|hi|hey|thanks|thank you|ok|okay|yes|no|bye)[。.!！?？]*$",
    re.IGNORECASE,
)
_ASSISTANT_FILLER = re.compile(
    r"欢迎来到|welcome to our office|我是\s*Sara|I am Sara|"
    r"哦[，,]?我来看看|嗯哼[，,]?我来看看|我抓到重点了|"
    r"本轮未完成|需要重新尝试",
    re.IGNORECASE,
)
_PREFERENCE = re.compile(
    r"喜欢|偏好|更喜欢|不喜欢|感兴趣|希望|倾向|认为|觉得|担心|在意|"
    r"\bprefer\b|\blike\b|\bdislike\b|\binterested in\b|\bhope\b|"
    r"\bthink\b|\bbelieve\b|\bconcerned\b",
    re.IGNORECASE,
)
_BACKGROUND = re.compile(
    r"我是|我从事|我负责|我主要|我们公司|我们团队|我的工作|我的行业|"
    r"\bI am\b|\bI'm\b|\bI work\b|\bmy job\b|\bour company\b|\bour team\b",
    re.IGNORECASE,
)
_REQUEST = re.compile(
    r"打开|关闭|开始|停止|播放|创建|发送|预约|登记|查看|总结|解释|调整|设置|帮我|需要|想要|"
    r"\bopen\b|\bclose\b|\bstart\b|\bstop\b|\bplay\b|\bcreate\b|\bsend\b|"
    r"\bbook\b|\bshow\b|\bsummarize\b|\bsummarise\b|\bexplain\b|\bset\b|\bneed\b|\bwant\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"[？?]$|^(?:什么|为什么|怎么|如何|哪里|谁|多少|能不能|可以吗|请问)|"
    r"^(?:what|why|how|where|who|when|which|can|could|would|is|are|do|does)\b",
    re.IGNORECASE,
)


def _clean(value: Any, maximum: int = 180) -> str:
    text = " ".join(str(value or "").strip().split())
    text = re.sub(
        r"^(?:啊|哦|嗯哼?|好嘞|好的|好[，,]|行[，,]|当然可以[，,]?|"
        r"ah|oh|mm-hm|right|well|all right)[\s，,。.!-]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text[:maximum].rstrip(" ，,。.!！")


def _fingerprint(role: str, text: str) -> str:
    normalized = re.sub(r"[\W_]+", "", text).casefold()
    return f"{role}:{normalized}"


def _append_unique(values: list[str], value: str, maximum: int) -> None:
    clean = _clean(value, maximum)
    if not clean:
        return
    fingerprint = re.sub(r"[\W_]+", "", clean).casefold()
    if not fingerprint:
        return
    for existing in values:
        prior = re.sub(r"[\W_]+", "", existing).casefold()
        if fingerprint == prior or fingerprint in prior or prior in fingerprint:
            return
    values.append(clean)


def _substantive_messages(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    useful: list[tuple[str, str]] = []
    seen: set[str] = set()
    for message in messages[-96:]:
        role = str(message.get("role") or "").strip().casefold()
        text = _clean(message.get("text"), 500)
        if role not in {"user", "assistant"} or not text:
            continue
        if _GREETING_ONLY.fullmatch(text):
            continue
        if role == "assistant" and _ASSISTANT_FILLER.search(text):
            continue
        key = _fingerprint(role, text)
        if key in seen:
            continue
        seen.add(key)
        useful.append((role, text))
    return useful


def _enhanced_summarise_messages(messages: list[dict[str, Any]]) -> dict[str, list[str]]:
    useful = _substantive_messages(messages)
    joined = " ".join(text for _, text in useful).casefold()

    feature_map = [
        (r"powerpoint|ppt|幻灯片", "PowerPoint 语音控制"),
        (r"outlook|邮件|草稿", "Outlook 邮件助手"),
        (r"teams|会议软件", "Teams 与会议协作"),
        (r"录音|recording", "现场录音与总结"),
        (r"登记|联系方式|contact", "访客登记"),
        (r"预约|日历|meeting|calendar", "会议预约"),
        (r"智能接待|虚拟人|sara|接待", "智能接待"),
    ]
    interests = [label for pattern, label in feature_map if re.search(pattern, joined, re.I)]

    user_points: list[str] = []
    actions: list[str] = []
    for role, text in useful:
        if role != "user":
            continue
        if _BACKGROUND.search(text):
            label = "访客背景："
        elif _PREFERENCE.search(text):
            label = "访客观点或偏好："
        elif _QUESTION.search(text):
            label = "访客询问："
        elif _REQUEST.search(text):
            label = "访客提出："
        else:
            label = "访客提到："
        _append_unique(user_points, f"{label}{text}", 150)
        if _REQUEST.search(text):
            _append_unique(actions, text, 110)
        if len(user_points) >= 6:
            break

    assistant_actions: list[str] = []
    for role, text in useful:
        if role != "assistant":
            continue
        if re.search(
            r"已经|已打开|已保存|已创建|已发送|已调整|已跳转|已翻到|成功|完成并验证|"
            r"\bopened\b|\bcreated\b|\bsent\b|\badjusted\b|\bcompleted\b|\bverified\b",
            text,
            re.IGNORECASE,
        ):
            _append_unique(assistant_actions, text, 120)
        if len(assistant_actions) >= 2:
            break

    follow_ups: list[str] = []
    if re.search(r"预约|安排.*会议|\bmeeting\b", joined, re.I):
        follow_ups.append("跟进已选择或计划中的会议预约。")
    if re.search(r"联系|邮箱|电话|登记|\bcontact\b", joined, re.I):
        follow_ups.append("根据访客授权信息进行后续联系。")
    if re.search(r"待确认|后续确认|进一步|follow.?up|confirm later", joined, re.I):
        follow_ups.append("确认对话中尚未解决的问题。")

    bullets = user_points[:6]
    if assistant_actions:
        bullets.append(f"系统已完成或确认：{'；'.join(assistant_actions)}。")
    if follow_ups:
        bullets.append(f"建议后续：{'；'.join(follow_ups)}")
    if not bullets:
        bullets.append("本 Session 暂无可提炼的实质对话要点。")

    return {
        "bullet_points": bullets[:8],
        "interests": interests[:12],
        "actions": actions[:12],
        "follow_ups": follow_ups[:8],
    }


def _timestamp_value(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    clean = str(value or "").strip()
    return clean[:100] or None


def _merged_messages(req: Any) -> list[Any]:
    raw: list[dict[str, Any]] = [item.model_dump() for item in req.messages]
    snapshot = conversation_store.snapshot(req.conversation_id)
    if isinstance(snapshot, dict):
        current_visit = str(snapshot.get("visit_id") or "").strip()
        if not current_visit or current_visit == req.visit_id:
            state_messages = snapshot.get("messages")
            if isinstance(state_messages, list):
                raw.extend(item for item in state_messages if isinstance(item, dict))

    merged: list[Any] = []
    seen: set[str] = set()
    for item in raw:
        role = str(item.get("role") or "").strip().casefold()
        text = _clean(item.get("text"), 4_000)
        if role not in {"user", "assistant", "system"} or not text:
            continue
        key = _fingerprint(role, text)
        if key in seen:
            continue
        seen.add(key)
        merged.append(
            contact_record_api.SummaryMessage(
                role=role,
                text=text,
                timestamp=_timestamp_value(item.get("timestamp")),
                source=_clean(item.get("source"), 160) or None,
            )
        )
    return merged[-64:]


def _merged_upsert_summary(req: Any) -> dict[str, Any]:
    merged = _merged_messages(req)
    return _original_upsert_summary(req.model_copy(update={"messages": merged}))


contact_record_api._summarise_messages = _enhanced_summarise_messages
contact_record_api._upsert_summary = _merged_upsert_summary
