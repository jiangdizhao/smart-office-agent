from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.semantic_deterministic_grammar import classify_deterministic as classify_base
from app.semantic_route_models import SemanticAction, SemanticRoute, SemanticRouteRequest


@dataclass(frozen=True)
class _Target:
    name: str
    pattern: re.Pattern[str]
    intent: str
    domain: str
    risk: str


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("(?:" + "|".join(parts) + ")", re.IGNORECASE)


_TARGETS = (
    _Target("teams", _rx(r"(?:microsoft\s*)?teams", r"微软团队", r"teams客户端"), "application_action", "office", "low"),
    _Target("onenote", _rx(r"one\s*note", r"onenote", r"微软笔记", r"数字笔记"), "application_action", "office", "low"),
    _Target("powerpoint", _rx(r"power\s*point", r"powerpoint", r"ppt", r"幻灯片", r"演示文稿"), "presentation_action", "office", "low"),
    _Target("music", _rx(r"music", r"song", r"歌曲", r"音乐", r"媒体播放器", r"media\s*player"), "system_action", "office", "low"),
    _Target(
        "system_volume",
        _rx(
            r"系统音量", r"电脑音量", r"扬声器音量", r"扬声器声音", r"喇叭音量", r"喇叭声音",
            r"系统声音", r"电脑声音", r"播放音量", r"音量", r"声音大小", r"声音",
            r"speaker\s+volume", r"system\s+volume", r"sound\s+level", r"volume",
        ),
        "system_action",
        "office",
        "low",
    ),
    _Target("meeting_booking", _rx(r"预约(?:会议|演示|时间)?", r"会议预约", r"预约日历", r"会议日历", r"book(?:ing)?\s+(?:a\s+)?meeting", r"schedule\s+(?:a\s+)?meeting", r"appointment"), "open_meeting_booking", "interaction", "business_state"),
    _Target("contact_registration", _rx(r"访客登记", r"登记信息", r"登记表", r"联系信息", r"联系方式", r"个人信息表", r"registration\s+form", r"contact\s+form", r"visitor\s+form"), "open_contact_registration", "interaction", "business_state"),
    _Target("recording", _rx(r"实时录音", r"录音", r"录制", r"recording", r"record\s+(?:this|our)?\s*(?:conversation|discussion)?"), "open_recording", "interaction", "business_state"),
    _Target("transcript", _rx(r"对话总结", r"会话总结", r"聊天总结", r"对话记录", r"会话记录", r"session\s+summary", r"conversation\s+summary", r"transcript"), "open_transcript", "interaction", "low"),
    _Target("result_center", _rx(r"结果中心", r"已保存(?:结果|资料)", r"访客档案", r"联系人列表", r"录音列表", r"result\s+center", r"saved\s+results", r"visitor\s+profiles"), "open_result_center", "interaction", "low"),
)

_NEGATION = _rx(
    r"不要", r"先别", r"别打开", r"别启动", r"别播放", r"不用", r"无需", r"不需要",
    r"不想", r"不是要", r"暂时不", r"先不要", r"请勿", r"禁止",
    r"do\s+not", r"don't", r"dont", r"not\s+now", r"without", r"never",
)
_CONSULTATION = _rx(
    r"介绍", r"说明", r"解释", r"讲讲", r"说说", r"了解", r"咨询", r"问问", r"询问",
    r"想知道", r"能做什么", r"有什么功能", r"有什么用", r"用途", r"怎么用", r"如何使用",
    r"为什么", r"是什么", r"区别", r"比较", r"评价", r"优缺点", r"适合什么",
    r"explain", r"describe", r"tell\s+me", r"learn\s+about", r"know\s+about",
    r"what\s+(?:is|does|can)", r"why", r"how\s+(?:does|do|can)", r"compare",
    r"capabilit(?:y|ies)", r"feature(?:s)?", r"advantages?", r"difference",
)
_HYPOTHETICAL = _rx(
    r"如果", r"假如", r"假设", r"要是", r"会不会", r"可能会", r"万一",
    r"if\b", r"suppose", r"hypothetical", r"would\s+it", r"what\s+if",
)
_QUOTATION = _rx(
    r"他说", r"她说", r"别人说", r"刚才说", r"所谓", r"引号", r"原话",
    r"quoted", r"someone\s+said", r"they\s+said", r"the\s+phrase",
)

_OPEN = _rx(
    r"打开", r"开启", r"启动", r"运行", r"调出", r"进入", r"显示", r"开一下", r"开开",
    r"open\b", r"launch\b", r"start\b", r"show\b", r"display\b", r"bring\s+up",
)
_CLOSE = _rx(
    r"关闭", r"关掉", r"退出", r"结束", r"收起", r"停掉", r"关一下",
    r"close\b", r"quit\b", r"exit\b", r"turn\s+off", r"shut\s+down",
)
_PLAY = _rx(r"播放", r"放一首", r"放点", r"来一首", r"开始音乐", r"play\b")
_STOP = _rx(r"停止", r"停止播放", r"别放了", r"关音乐", r"stop\b", r"stop\s+playing")
_VOLUME_SET = _rx(
    r"设置(?:为|到|成)?", r"设(?:置)?(?:为|到|成)?", r"调(?:整)?(?:为|到|成)?", r"改(?:为|到|成)?",
    r"变成", r"调节到", r"控制在", r"set(?:\s+the)?", r"change(?:\s+the)?", r"adjust(?:\s+the)?",
)

_POLITE_REQUEST = _rx(
    r"请(?:你|您)?", r"麻烦(?:你|您)?", r"劳驾(?:你|您)?", r"拜托(?:你|您)?",
    r"帮(?:我|忙)?", r"替我", r"给我", r"能否", r"能不能", r"可否",
    r"你(?:能|可以|可不可以|能不能)", r"您(?:能|可以|可不可以|能不能)", r"可以(?:帮我|替我)?",
    r"please", r"can\s+you", r"could\s+you", r"would\s+you", r"will\s+you",
    r"would\s+you\s+mind", r"i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to",
)
_FILLER = _rx(
    r"请(?:你|您)?", r"麻烦(?:你|您)?", r"劳驾(?:你|您)?", r"拜托(?:你|您)?",
    r"帮(?:我|忙)?", r"替我", r"给我", r"你", r"您", r"能否", r"能不能", r"可否",
    r"可不可以", r"可以", r"能", r"我想让", r"我需要你", r"我希望你",
    r"一下", r"现在", r"立即", r"马上", r"当前", r"目前", r"好吗", r"可以吗", r"行吗", r"吧", r"呢", r"吗",
    r"please", r"can\s+you", r"could\s+you", r"would\s+you", r"will\s+you", r"would\s+you\s+mind",
    r"for\s+me", r"right\s+now", r"now", r"uh", r"um", r"erm", r"hmm", r"the", r"app",
)

_CHINESE_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_ENGLISH_NUMBERS = {
    "zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "one hundred": 100, "hundred": 100,
}


def _normalise(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(value.strip().split())


def _targets(text: str) -> list[_Target]:
    return [target for target in _TARGETS if target.pattern.search(text)]


def _verb(text: str, target: str) -> str | None:
    values: list[str] = []
    if target == "music":
        if _PLAY.search(text) or _OPEN.search(text):
            values.append("start")
        if _STOP.search(text) or _CLOSE.search(text):
            values.append("stop")
    else:
        if _OPEN.search(text):
            values.append("open")
        if _CLOSE.search(text):
            values.append("close")
    unique = list(dict.fromkeys(values))
    return unique[0] if len(unique) == 1 else None


def _action(
    verb: str,
    target: str,
    evidence: str,
    *,
    arguments: dict | None = None,
    negated: bool = False,
) -> SemanticAction:
    return SemanticAction(
        verb=verb,  # type: ignore[arg-type]
        target=target,
        arguments=dict(arguments or {}),
        polarity="negated" if negated else "affirmed",
        speech_act="explanation_request" if negated else "command",
        evidence=evidence,
    )


def _parse_chinese_number(value: str) -> int | None:
    clean = value.strip()
    if not clean:
        return None
    if clean == "百" or clean == "一百":
        return 100
    if "十" in clean:
        left, _, right = clean.partition("十")
        tens = 1 if left == "" else _CHINESE_DIGITS.get(left)
        ones = 0 if right == "" else _CHINESE_DIGITS.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(clean) == 1:
        return _CHINESE_DIGITS.get(clean)
    digits = [_CHINESE_DIGITS.get(char) for char in clean]
    if any(digit is None for digit in digits):
        return None
    return int("".join(str(digit) for digit in digits))


def _extract_volume_percent(text: str) -> int | None:
    if _rx(r"最大", r"最高", r"满格", r"百分之百", r"百分百", r"maximum", r"full\s+volume").search(text):
        return 100
    if _rx(r"最小", r"最低", r"完全静音", r"minimum").search(text):
        return 0
    if _rx(r"一半", r"半音量", r"half(?:\s+volume)?").search(text):
        return 50

    arabic = re.search(r"(?:百分之\s*)?(\d{1,3})\s*(?:%|percent|个百分点)?", text, re.IGNORECASE)
    if arabic:
        return int(arabic.group(1))

    proportion = re.search(r"([一二两三四五六七八九])成", text)
    if proportion:
        value = _CHINESE_DIGITS.get(proportion.group(1))
        return None if value is None else value * 10

    chinese = re.search(r"(?:百分之\s*)?([零〇一二两三四五六七八九十百]{1,4})", text)
    if chinese:
        return _parse_chinese_number(chinese.group(1))

    for word, value in sorted(_ENGLISH_NUMBERS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE):
            return value
    return None


def _volume_route(request: SemanticRouteRequest, text: str, targets: list[_Target]) -> SemanticRoute | None:
    if not any(target.name == "system_volume" for target in targets):
        return None
    if _NEGATION.search(text) or _CONSULTATION.search(text) or _HYPOTHETICAL.search(text) or _QUOTATION.search(text):
        return None
    percent = _extract_volume_percent(text)
    has_command_shape = bool(_VOLUME_SET.search(text) or _POLITE_REQUEST.search(text) or percent is not None)
    if percent is None or not has_command_shape:
        return None
    if not 0 <= percent <= 100:
        return SemanticRoute(
            primary_intent="system_action",
            domain="office",
            action_mode="clarify",
            confidence=1.0,
            requires_clarification=True,
            clarification_question=(
                "请提供 0 到 100 之间的音量百分比。"
                if request.language == "zh"
                else "Please provide a volume percentage from 0 to 100."
            ),
            entities={"language": request.language, "percent": percent},
            risk="none",
            reason_codes=["deterministic_volume_out_of_range"],
            source="fast_path",
            complexity="not_applicable",
            answer_engine="backend",
        )
    return SemanticRoute(
        primary_intent="system_action",
        domain="office",
        action_mode="execute",
        confidence=1.0,
        actions=[_action("set", "system_volume", request.text, arguments={"percent": percent})],
        entities={"language": request.language, "percent": percent, "grammar": "high_coverage_volume"},
        risk="low",
        reason_codes=["deterministic_high_coverage_volume_grammar"],
        source="fast_path",
        complexity="not_applicable",
        answer_engine="office_interpreter",
    )


def _discussion_route(request: SemanticRouteRequest, text: str, targets: list[_Target]) -> SemanticRoute | None:
    if not targets:
        return None
    negated = bool(_NEGATION.search(text))
    consultation = bool(_CONSULTATION.search(text))
    hypothetical = bool(_HYPOTHETICAL.search(text))
    quotation = bool(_QUOTATION.search(text))
    if not (negated or consultation or hypothetical or quotation):
        return None

    negated_actions: list[SemanticAction] = []
    if negated:
        for target in targets:
            verb = _verb(text, target.name)
            if verb:
                negated_actions.append(_action(verb, target.name, request.text, negated=True))

    intent = "capability_explanation" if consultation or negated else "general_question"
    return SemanticRoute(
        primary_intent=intent,
        domain="general",
        action_mode="answer_only",
        confidence=1.0,
        negated_actions=negated_actions,
        entities={
            "language": request.language,
            "targets": [target.name for target in targets],
            "negation": negated,
            "consultation": consultation,
            "hypothetical": hypothetical,
            "quotation": quotation,
        },
        risk="none",
        reason_codes=["deterministic_safe_discussion_precedence"],
        source="fast_path",
        complexity="simple",
        answer_engine="backend",
    )


def _polite_command_route(request: SemanticRouteRequest, text: str, targets: list[_Target]) -> SemanticRoute | None:
    if len(targets) != 1 or not _POLITE_REQUEST.search(text):
        return None
    if targets[0].name == "system_volume":
        return None
    if _NEGATION.search(text) or _CONSULTATION.search(text) or _HYPOTHETICAL.search(text) or _QUOTATION.search(text):
        return None
    target = targets[0]
    verb = _verb(text, target.name)
    if verb is None:
        return None

    remainder = target.pattern.sub(" ", text)
    remainder = (_PLAY if verb == "start" else _STOP if verb == "stop" else _OPEN if verb == "open" else _CLOSE).sub(" ", remainder)
    remainder = _FILLER.sub(" ", remainder)
    remainder = re.sub(r"[^\w\u3400-\u9fff%]+", " ", remainder)
    if " ".join(remainder.split()):
        return None

    return SemanticRoute(
        primary_intent=target.intent,  # type: ignore[arg-type]
        domain=target.domain,  # type: ignore[arg-type]
        action_mode="execute",
        confidence=1.0,
        actions=[_action(verb, target.name, request.text)],
        entities={"language": request.language, "polite_request": True},
        risk=target.risk,  # type: ignore[arg-type]
        reason_codes=["deterministic_polite_command_precedence"],
        source="fast_path",
        complexity="not_applicable",
        answer_engine="office_interpreter" if target.domain == "office" else "realtime",
    )


def classify_deterministic(request: SemanticRouteRequest) -> SemanticRoute | None:
    text = _normalise(request.text)
    targets = _targets(text)

    # Negation, consultation, hypothetical wording and quotations always win over
    # command-shaped words. Volume and polite commands are then handled before the
    # narrower base grammar so natural exhibition speech does not fall to a model.
    discussion = _discussion_route(request, text, targets)
    if discussion is not None:
        return discussion
    volume = _volume_route(request, text, targets)
    if volume is not None:
        return volume
    polite = _polite_command_route(request, text, targets)
    if polite is not None:
        return polite
    return classify_base(request)
