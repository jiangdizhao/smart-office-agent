from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app import contact_record_api
from app.conversation_store import conversation_store

_original_upsert_summary = contact_record_api._upsert_summary
_original_summary_row = contact_record_api._summary_row

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
_OFFICE_TERMS = re.compile(
    r"powerpoint|ppt|幻灯片|演示文稿|outlook|邮件|草稿|teams|onenote|"
    r"音量|亮度|音乐|录音|登记|联系方式|预约|结果中心|"
    r"presentation|email|volume|brightness|recording|registration|booking",
    re.IGNORECASE,
)
_REQUEST = re.compile(
    r"打开|关闭|开始|停止|播放|创建|发送|预约|登记|查看|总结|解释|调整|设置|"
    r"帮我|需要|想要|希望|"
    r"\bopen\b|\bclose\b|\bstart\b|\bstop\b|\bplay\b|\bcreate\b|\bsend\b|"
    r"\bbook\b|\bshow\b|\bsummarize\b|\bsummarise\b|\bexplain\b|\bset\b|"
    r"\bneed\b|\bwant\b|\bhope\b",
    re.IGNORECASE,
)
_SUCCESS = re.compile(
    r"已经|已打开|已保存|已创建|已发送|已调整|已跳转|已翻到|成功|完成并验证|"
    r"\bopened\b|\bcreated\b|\bsent\b|\badjusted\b|\bcompleted\b|\bverified\b",
    re.IGNORECASE,
)


def _clean(value: Any, maximum: int = 220) -> str:
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


def _append_unique(values: list[str], value: str, maximum: int = 180) -> None:
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


def _substantive_messages(messages: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    useful: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for message in messages[-128:]:
        role = str(message.get("role") or "").strip().casefold()
        text = _clean(message.get("text"), 600)
        timestamp = str(message.get("timestamp") or "").strip()
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
        useful.append((role, text, timestamp))
    return useful


def _sentences(text: str) -> list[str]:
    return [
        _clean(item, 180)
        for item in re.split(r"(?<=[。！？.!?])\s*|[；;]+", text)
        if _clean(item, 180)
    ]


def _extract_first(patterns: list[re.Pattern[str]], texts: list[str]) -> str:
    for text in texts:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return _clean(match.group(1), 80)
    return ""


def _extract_profile(user_texts: list[str]) -> dict[str, Any]:
    occupation = _extract_first(
        [
            re.compile(r"(?:我是|我是一名|我的职业是|我从事|我做)\s*([^，。！？]{2,40})"),
            re.compile(r"\b(?:I am|I'm|I work as|my role is)\s+(?:an?\s+)?([^,.!?]{2,60})", re.I),
        ],
        user_texts,
    )
    industry = _extract_first(
        [
            re.compile(r"(?:我们公司|我所在公司|我们团队).{0,10}(?:做|从事|属于)\s*([^，。！？]{2,40})"),
            re.compile(r"(?:在|属于)\s*([^，。！？]{2,24}行业)"),
            re.compile(r"\b(?:our company is in|we work in|my industry is)\s+([^,.!?]{2,60})", re.I),
        ],
        user_texts,
    )

    responsibilities: list[str] = []
    goals: list[str] = []
    pain_points: list[str] = []
    preferences: list[str] = []
    concerns: list[str] = []
    casual_topics: list[str] = []
    questions: list[str] = []

    for text in user_texts:
        for sentence in _sentences(text):
            if re.search(r"我(?:主要)?负责|我的工作|日常|职责|I am responsible|my work|day-to-day", sentence, re.I):
                _append_unique(responsibilities, sentence, 120)
            if re.search(r"希望|想要|需要|目标|打算|计划|I hope|I want|I need|my goal|plan to", sentence, re.I):
                _append_unique(goals, sentence, 120)
            if re.search(r"问题|麻烦|头疼|困难|痛点|耗时|浪费时间|容易忘|遗漏|卡住|problem|difficult|pain point|time-consuming|forget|miss", sentence, re.I):
                _append_unique(pain_points, sentence, 120)
            if re.search(r"喜欢|偏好|更喜欢|不喜欢|倾向|认为|觉得|感兴趣|prefer|like|dislike|think|believe|interested", sentence, re.I):
                _append_unique(preferences, sentence, 120)
            if re.search(r"担心|在意|顾虑|隐私|价格|费用|成本|贵|concern|worried|privacy|price|cost|expensive", sentence, re.I):
                _append_unique(concerns, sentence, 120)
            if sentence.endswith(("？", "?")) or re.match(r"^(什么|为什么|怎么|如何|哪里|谁|多少|请问|what|why|how|where|who|when)", sentence, re.I):
                _append_unique(questions, sentence, 120)
            if (
                not _OFFICE_TERMS.search(sentence)
                and not _REQUEST.search(sentence)
                and len(sentence) >= 6
            ):
                _append_unique(casual_topics, sentence, 120)

    joined = " ".join(user_texts)
    contact_intent = "未明确"
    if re.search(r"愿意|可以联系|留下联系方式|打开登记|登记信息|yes.*contact|contact me|leave my details", joined, re.I):
        contact_intent = "愿意进一步联系或登记"
    elif re.search(r"不愿意|不要联系|不留联系方式|no contact|do not contact", joined, re.I):
        contact_intent = "明确拒绝后续联系"

    purchase_intent = "未明确"
    if re.search(r"价格|报价|多少钱|费用|采购|购买|部署|方案|price|quote|buy|purchase|deploy", joined, re.I):
        purchase_intent = "正在评估价格、方案或部署"
    elif re.search(r"演示|体验|试一下|demo|try it", joined, re.I):
        purchase_intent = "愿意体验或进一步了解"

    return {
        "occupation": occupation,
        "industry": industry,
        "responsibilities": responsibilities[:4],
        "goals": goals[:4],
        "pain_points": pain_points[:4],
        "preferences": preferences[:4],
        "concerns": concerns[:4],
        "casual_topics": casual_topics[:4],
        "questions": questions[:4],
        "contact_intent": contact_intent,
        "purchase_intent": purchase_intent,
    }


def _concise_items(values: list[str], maximum: int = 3) -> str:
    return "；".join(values[:maximum])


def _overview(profile: dict[str, Any], interests: list[str]) -> str:
    parts: list[str] = []
    occupation = str(profile.get("occupation") or "")
    industry = str(profile.get("industry") or "")
    if occupation and industry:
        parts.append(f"访客从事{occupation}，所在领域为{industry}")
    elif occupation:
        parts.append(f"访客从事{occupation}")
    elif industry:
        parts.append(f"访客所在领域为{industry}")
    else:
        parts.append("本次对话已形成可识别的访客需求与交流主题")

    responsibilities = list(profile.get("responsibilities") or [])
    goals = list(profile.get("goals") or [])
    pain_points = list(profile.get("pain_points") or [])
    preferences = list(profile.get("preferences") or [])
    casual = list(profile.get("casual_topics") or [])
    if responsibilities:
        parts.append(f"其工作内容主要涉及{_concise_items(responsibilities, 2)}")
    if pain_points:
        parts.append(f"当前主要困扰是{_concise_items(pain_points, 2)}")
    if goals:
        parts.append(f"希望实现{_concise_items(goals, 2)}")
    if interests:
        parts.append(f"对{'、'.join(interests[:4])}表现出关注")
    if preferences:
        parts.append(f"表达了这些观点或偏好：{_concise_items(preferences, 2)}")
    if casual:
        parts.append(f"闲聊还涉及{_concise_items(casual, 2)}")
    return "。".join(parts) + "。"


def _enhanced_summarise_messages(messages: list[dict[str, Any]]) -> dict[str, list[str]]:
    useful = _substantive_messages(messages)
    user_texts = [text for role, text, _ in useful if role == "user"]
    joined = " ".join(user_texts).casefold()

    feature_map = [
        (r"powerpoint|ppt|幻灯片", "PowerPoint 语音控制"),
        (r"outlook|邮件|草稿", "Outlook 邮件助手"),
        (r"teams|会议软件", "Teams 与会议协作"),
        (r"录音|recording", "现场录音与总结"),
        (r"登记|联系方式|contact", "访客登记"),
        (r"预约|日历|meeting|calendar", "会议预约"),
        (r"智能接待|虚拟人|sara|接待", "智能接待"),
    ]
    interests = [
        label
        for pattern, label in feature_map
        if re.search(pattern, joined, re.I)
        and not re.search(rf"(?:不喜欢|不需要|不要).{{0,12}}(?:{pattern})", joined, re.I)
    ]

    profile = _extract_profile(user_texts)
    actions: list[str] = []
    for text in user_texts:
        if _REQUEST.search(text):
            _append_unique(actions, text, 120)

    assistant_actions: list[str] = []
    for role, text, _ in useful:
        if role == "assistant" and _SUCCESS.search(text):
            _append_unique(assistant_actions, text, 130)

    follow_ups: list[str] = []
    if profile["contact_intent"] == "愿意进一步联系或登记":
        follow_ups.append("根据访客授权完成联系方式登记与后续联系。")
    if re.search(r"预约|安排.*会议|\bmeeting\b", joined, re.I):
        follow_ups.append("跟进已选择或计划中的会议预约。")
    if profile["pain_points"] or profile["goals"]:
        follow_ups.append("围绕访客的核心痛点和目标准备针对性方案。")
    if profile["concerns"]:
        follow_ups.append("回应访客对价格、隐私或实施范围的顾虑。")

    bullets: list[str] = [_overview(profile, interests)]
    traits: list[str] = []
    if profile["occupation"]:
        traits.append(f"职业/角色：{profile['occupation']}")
    if profile["industry"]:
        traits.append(f"行业：{profile['industry']}")
    if profile["responsibilities"]:
        traits.append(f"职责：{_concise_items(profile['responsibilities'], 2)}")
    if traits:
        bullets.append(f"用户特征：{'；'.join(traits)}。")
    if profile["goals"] or profile["pain_points"]:
        values: list[str] = []
        if profile["goals"]:
            values.append(f"目标：{_concise_items(profile['goals'], 2)}")
        if profile["pain_points"]:
            values.append(f"痛点：{_concise_items(profile['pain_points'], 2)}")
        bullets.append(f"核心需求：{'；'.join(values)}。")
    if profile["preferences"] or profile["concerns"]:
        values = []
        if profile["preferences"]:
            values.append(f"偏好/观点：{_concise_items(profile['preferences'], 2)}")
        if profile["concerns"]:
            values.append(f"顾虑：{_concise_items(profile['concerns'], 2)}")
        bullets.append(f"态度与偏好：{'；'.join(values)}。")
    if profile["casual_topics"] or profile["questions"]:
        values = []
        if profile["casual_topics"]:
            values.append(f"闲聊主题：{_concise_items(profile['casual_topics'], 3)}")
        if profile["questions"]:
            values.append(f"主要询问：{_concise_items(profile['questions'], 2)}")
        bullets.append(f"对话主题：{'；'.join(values)}。")
    bullets.append(
        f"商业与联系意向：{profile['purchase_intent']}；{profile['contact_intent']}。"
    )
    if assistant_actions:
        bullets.append(f"系统已完成或确认：{_concise_items(assistant_actions, 3)}。")
    elif actions:
        bullets.append(f"访客提出的主要请求：{_concise_items(actions, 3)}。")

    if not user_texts:
        bullets = ["当前 Session 尚未记录到可用于总结的访客实质发言。"]

    return {
        "bullet_points": bullets[:8],
        "interests": interests[:12],
        "actions": actions[:12],
        "follow_ups": follow_ups[:8],
        "_overview": [bullets[0]],
        "_visitor_profile": [json.dumps(profile, ensure_ascii=False)],
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

    merged_raw: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, item in enumerate(raw):
        role = str(item.get("role") or "").strip().casefold()
        text = _clean(item.get("text"), 4_000)
        if role not in {"user", "assistant", "system"} or not text:
            continue
        key = _fingerprint(role, text)
        if key in seen:
            continue
        seen.add(key)
        merged_raw.append(
            {
                "role": role,
                "text": text,
                "timestamp": _timestamp_value(item.get("timestamp")),
                "source": _clean(item.get("source"), 160) or None,
                "position": position,
            }
        )

    merged_raw.sort(key=lambda item: (item["timestamp"] or "9999", item["position"]))
    return [
        contact_record_api.SummaryMessage(
            role=item["role"],
            text=item["text"],
            timestamp=item["timestamp"],
            source=item["source"],
        )
        for item in merged_raw[-64:]
    ]


def _initialise_profile_table() -> None:
    contact_record_api._initialise()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_summary_profiles (
                summary_id TEXT PRIMARY KEY,
                overview TEXT NOT NULL,
                visitor_profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(summary_id) REFERENCES session_summaries(summary_id) ON DELETE CASCADE
            )
            """
        )
        connection.commit()


def _save_extended_profile(summary_id: str, synthesis: dict[str, list[str]]) -> dict[str, Any]:
    _initialise_profile_table()
    overview = str((synthesis.get("_overview") or [""])[0])
    raw_profile = str((synthesis.get("_visitor_profile") or ["{}"])[0])
    try:
        profile = json.loads(raw_profile)
    except (TypeError, ValueError, json.JSONDecodeError):
        profile = {}
    now = contact_record_api._now_iso()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        connection.execute(
            """
            INSERT INTO session_summary_profiles (
                summary_id, overview, visitor_profile_json, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(summary_id) DO UPDATE SET
                overview = excluded.overview,
                visitor_profile_json = excluded.visitor_profile_json,
                updated_at = excluded.updated_at
            """,
            (summary_id, overview, json.dumps(profile, ensure_ascii=False), now),
        )
        connection.commit()
    return {"overview": overview, "visitor_profile": profile}


def _load_extended_profile(summary_id: str) -> dict[str, Any]:
    _initialise_profile_table()
    with contact_record_api._LOCK, contact_record_api._connect() as connection:
        row = connection.execute(
            "SELECT overview, visitor_profile_json FROM session_summary_profiles WHERE summary_id = ?",
            (summary_id,),
        ).fetchone()
    if row is None:
        return {"overview": "", "visitor_profile": {}}
    try:
        profile = json.loads(str(row["visitor_profile_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        profile = {}
    return {"overview": str(row["overview"] or ""), "visitor_profile": profile}


def _profiled_summary_row(row: Any) -> dict[str, Any]:
    result = _original_summary_row(row)
    result.update(_load_extended_profile(str(result.get("summary_id") or "")))
    return result


def _merged_upsert_summary(req: Any) -> dict[str, Any]:
    merged = _merged_messages(req)
    synthesis = _enhanced_summarise_messages([item.model_dump() for item in merged])
    payload = _original_upsert_summary(req.model_copy(update={"messages": merged}))
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if isinstance(summary, dict):
        extended = _save_extended_profile(str(summary.get("summary_id") or ""), synthesis)
        summary.update(extended)
    return payload


contact_record_api._summarise_messages = _enhanced_summarise_messages
contact_record_api._summary_row = _profiled_summary_row
contact_record_api._upsert_summary = _merged_upsert_summary
