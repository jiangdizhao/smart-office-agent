from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from app.semantic_route_models import SemanticAction, SemanticRoute, SemanticRouteRequest


@dataclass(frozen=True)
class TargetSpec:
    target: str
    pattern: re.Pattern[str]
    intent: str
    domain: str


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("(?:" + "|".join(parts) + ")", re.IGNORECASE)


_TARGETS: tuple[TargetSpec, ...] = (
    TargetSpec("teams", _rx(r"(?:microsoft\s*)?teams", r"微软团队", r"团队会议"), "application_action", "office"),
    TargetSpec("onenote", _rx(r"one\s*note", r"onenote", r"微软笔记", r"数字笔记"), "application_action", "office"),
    TargetSpec("powerpoint", _rx(r"power\s*point", r"powerpoint", r"ppt", r"幻灯片", r"演示文稿"), "presentation_action", "office"),
    TargetSpec("music", _rx(r"music", r"song", r"歌曲", r"音乐", r"放歌", r"听歌"), "system_action", "office"),
    TargetSpec("system_volume", _rx(r"系统音量", r"电脑音量", r"扬声器音量", r"音量", r"volume"), "system_action", "office"),
    TargetSpec("meeting_booking", _rx(r"预约(?:会议|演示|时间)?", r"会议预约", r"预约日历", r"会议日历", r"book(?:ing)?\s+(?:a\s+)?meeting", r"schedule\s+(?:a\s+)?meeting", r"appointment"), "open_meeting_booking", "interaction"),
    TargetSpec("contact_registration", _rx(r"访客登记", r"登记信息", r"登记表", r"联系信息", r"联系方式", r"个人信息表", r"registration\s+form", r"contact\s+form", r"visitor\s+form"), "open_contact_registration", "interaction"),
    TargetSpec("recording", _rx(r"实时录音", r"录音", r"录制", r"recording", r"record\s+(?:this|our)?\s*(?:conversation|discussion)?"), "open_recording", "interaction"),
    TargetSpec("transcript", _rx(r"对话总结", r"会话总结", r"聊天总结", r"对话记录", r"会话记录", r"session\s+summary", r"conversation\s+summary", r"transcript"), "open_transcript", "interaction"),
    TargetSpec("result_center", _rx(r"结果中心", r"已保存(?:结果|资料)", r"访客档案", r"联系人列表", r"录音列表", r"result\s+center", r"saved\s+results", r"visitor\s+profiles"), "open_result_center", "interaction"),
)

_IDENTITY_SUBJECT = _rx(r"你", r"您", r"sara", r"这个(?:数字)?人", r"这个(?:ai|人工智能)", r"your", r"you")
_IDENTITY_ATTRIBUTE = _rx(
    r"是谁",
    r"什么身份",
    r"身份是什么",
    r"什么角色",
    r"角色是什么",
    r"职责",
    r"负责什么",
    r"主要负责",
    r"做什么",
    r"工作是什么",
    r"定位",
    r"自我介绍",
    r"介绍(?:一下)?(?:你|自己)",
    r"role",
    r"responsibilit(?:y|ies)",
    r"position",
    r"what\s+(?:are|is)\s+you",
    r"what\s+do\s+you\s+do",
    r"who\s+are\s+you",
    r"introduce\s+yourself",
    r"tell\s+me\s+about\s+yourself",
)

_NEGATION = _rx(
    r"不要",
    r"先别",
    r"别",
    r"不用",
    r"无需",
    r"不需要",
    r"不想",
    r"不是要",
    r"暂时不",
    r"先不要",
    r"请勿",
    r"do\s+not",
    r"don't",
    r"dont",
    r"not\s+now",
    r"without",
)
_EXPLANATION = _rx(
    r"介绍",
    r"说明",
    r"解释",
    r"讲讲",
    r"说说",
    r"能做什么",
    r"有什么功能",
    r"有什么用",
    r"用途",
    r"怎么用",
    r"如何使用",
    r"为什么",
    r"是什么",
    r"区别",
    r"比较",
    r"explain",
    r"describe",
    r"tell\s+me",
    r"what\s+(?:is|does|can)",
    r"why",
    r"how\s+(?:does|do|can)",
    r"compare",
    r"capabilit(?:y|ies)",
    r"feature(?:s)?",
)
_HYPOTHETICAL = _rx(
    r"如果",
    r"假如",
    r"假设",
    r"要是",
    r"能否",
    r"会不会",
    r"可能",
    r"if\b",
    r"suppose",
    r"hypothetical",
    r"would\s+it",
    r"what\s+if",
)
_QUOTATION = _rx(r"他说", r"她说", r"别人说", r"所谓", r"引号", r"quoted", r"someone\s+said", r"they\s+said")
_QUESTION = _rx(r"[?？]", r"吗\b", r"呢\b", r"么\b", r"what", r"why", r"how", r"can\s+you", r"could\s+you", r"would\s+you")

_OPEN = _rx(r"打开", r"开启", r"启动", r"运行", r"调出", r"进入", r"显示", r"open\b", r"launch\b", r"start\b", r"show\b", r"display\b")
_CLOSE = _rx(r"关闭", r"关掉", r"退出", r"结束", r"收起", r"close\b", r"quit\b", r"exit\b", r"turn\s+off")
_PLAY = _rx(r"播放", r"放一首", r"放点", r"来一首", r"play\b")
_STOP = _rx(r"停止", r"别放了", r"不要播放", r"stop\b")
_NEXT = _rx(r"下一页", r"下一张", r"下一个幻灯片", r"next\s+slide")
_PREVIOUS = _rx(r"上一页", r"上一张", r"上一个幻灯片", r"previous\s+slide", r"back\s+one\s+slide")
_SET_VOLUME = re.compile(
    r"(?:把|将)?(?:系统|电脑|扬声器)?音量(?:调到|调成|设置为|设为|改为|变成)\s*(\d{1,3})\s*%?"
    r"|set\s+(?:the\s+)?volume\s+to\s+(\d{1,3})\s*%?",
    re.IGNORECASE,
)

_COMMAND_FILLER = _rx(
    r"请",
    r"帮我",
    r"麻烦",
    r"给我",
    r"现在",
    r"立即",
    r"一下",
    r"可以吗",
    r"好吗",
    r"please",
    r"could\s+you",
    r"would\s+you",
    r"can\s+you",
    r"for\s+me",
    r"now",
    r"uh",
    r"um",
    r"erm",
    r"hmm",
    r"嗯",
    r"呃",
    r"啊",
)
_AMBIGUOUS_PRONOUN = _rx(r"那个", r"这个", r"它", r"刚才那个", r"前面那个", r"that\s+one", r"it\b", r"the\s+previous\s+one")


def _normalise(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(value.strip().split())


def _compact(text: str) -> str:
    return re.sub(r"[\s，。！？、;；:：,.!?]+", "", text)


def _targets(text: str) -> list[TargetSpec]:
    return [spec for spec in _TARGETS if spec.pattern.search(text)]


def _action(verb: str, target: str, evidence: str, *, arguments: dict | None = None, negated: bool = False) -> SemanticAction:
    return SemanticAction(
        verb=verb,  # type: ignore[arg-type]
        target=target,
        arguments=dict(arguments or {}),
        polarity="negated" if negated else "affirmed",
        speech_act="explanation_request" if negated else "command",
        evidence=evidence,
    )


def _identity_route(request: SemanticRouteRequest, text: str) -> SemanticRoute | None:
    compact = _compact(text)
    if not (_IDENTITY_ATTRIBUTE.search(text) or _IDENTITY_ATTRIBUTE.search(compact)):
        return None
    if not (_IDENTITY_SUBJECT.search(text) or _IDENTITY_SUBJECT.search(compact) or "自我介绍" in compact):
        return None
    return SemanticRoute(
        primary_intent="self_introduction",
        domain="identity",
        action_mode="answer_only",
        confidence=1.0,
        entities={"language": request.language, "grammar": "identity_role_responsibility"},
        risk="none",
        reason_codes=["deterministic_identity_grammar"],
        source="fast_path",
        complexity="simple",
        answer_engine="backend",
    )


def _volume_route(request: SemanticRouteRequest, text: str) -> SemanticRoute | None:
    match = _SET_VOLUME.fullmatch(text.strip(" ，。！？!?.,"))
    if not match:
        return None
    raw = next((group for group in match.groups() if group is not None), None)
    if raw is None:
        return None
    percent = int(raw)
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
            entities={"language": request.language},
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
        entities={"language": request.language, "percent": percent},
        risk="low",
        reason_codes=["deterministic_numeric_volume_grammar"],
        source="fast_path",
        complexity="not_applicable",
        answer_engine="office_interpreter",
    )


def _slide_route(request: SemanticRouteRequest, text: str) -> SemanticRoute | None:
    if _NEGATION.search(text) or _EXPLANATION.search(text) or _HYPOTHETICAL.search(text):
        return None
    verb = "next" if _NEXT.fullmatch(text.strip(" ，。！？!?.,")) else "previous" if _PREVIOUS.fullmatch(text.strip(" ，。！？!?.,")) else None
    if verb is None:
        return None
    return SemanticRoute(
        primary_intent="presentation_action",
        domain="office",
        action_mode="execute",
        confidence=1.0,
        actions=[_action(verb, "powerpoint", request.text)],
        entities={"language": request.language},
        risk="low",
        reason_codes=["deterministic_slide_navigation_grammar"],
        source="fast_path",
        complexity="not_applicable",
        answer_engine="office_interpreter",
    )


def _verb_for_target(text: str, target: str) -> str | None:
    candidates: list[str] = []
    if target == "music":
        if _PLAY.search(text) or _OPEN.search(text):
            candidates.append("start")
        if _STOP.search(text) or _CLOSE.search(text):
            candidates.append("stop")
    else:
        if _OPEN.search(text):
            candidates.append("open")
        if _CLOSE.search(text):
            candidates.append("close")
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


def _remainder_is_filler(text: str, specs: Iterable[TargetSpec], verb: str) -> bool:
    value = text
    for spec in specs:
        value = spec.pattern.sub(" ", value)
    patterns = {
        "open": _OPEN,
        "close": _CLOSE,
        "start": _rx(_OPEN.pattern, _PLAY.pattern),
        "stop": _rx(_CLOSE.pattern, _STOP.pattern),
    }
    value = patterns[verb].sub(" ", value)
    value = _COMMAND_FILLER.sub(" ", value)
    value = re.sub(r"[^\w\u3400-\u9fff%]+", " ", value)
    return not " ".join(value.split())


def _negative_or_discussion_route(request: SemanticRouteRequest, text: str, specs: list[TargetSpec]) -> SemanticRoute | None:
    if not specs:
        return None
    has_negation = bool(_NEGATION.search(text))
    has_explanation = bool(_EXPLANATION.search(text))
    has_hypothetical = bool(_HYPOTHETICAL.search(text))
    has_quotation = bool(_QUOTATION.search(text))
    has_question = bool(_QUESTION.search(text))
    if not (has_negation or has_explanation or has_hypothetical or has_quotation or has_question):
        return None

    negated_actions: list[SemanticAction] = []
    if has_negation:
        for spec in specs:
            verb = _verb_for_target(text, spec.target)
            if verb:
                negated_actions.append(_action(verb, spec.target, request.text, negated=True))

    primary = "capability_explanation" if has_explanation or has_negation else "general_question"
    return SemanticRoute(
        primary_intent=primary,
        domain="general",
        action_mode="answer_only",
        confidence=1.0,
        negated_actions=negated_actions,
        entities={
            "language": request.language,
            "targets": [spec.target for spec in specs],
            "negation": has_negation,
            "hypothetical": has_hypothetical,
            "quotation": has_quotation,
            "question": has_question,
        },
        risk="none",
        reason_codes=["deterministic_nonexecution_discussion_grammar"],
        source="fast_path",
        complexity="simple",
        answer_engine="backend",
    )


def _interaction_route(request: SemanticRouteRequest, text: str, specs: list[TargetSpec]) -> SemanticRoute | None:
    if len(specs) != 1 or specs[0].domain != "interaction":
        return None
    spec = specs[0]
    if _NEGATION.search(text) or _EXPLANATION.search(text) or _HYPOTHETICAL.search(text) or _QUOTATION.search(text):
        return None
    verb = _verb_for_target(text, spec.target)
    desire = _rx(r"我想", r"我要", r"希望", r"需要", r"帮我", r"请", r"麻烦", r"let\s+me", r"i\s+(?:want|need|would\s+like)", r"please").search(text)
    direct_booking = spec.target == "meeting_booking" and _rx(r"预约", r"预订", r"约个时间", r"安排会议", r"book", r"schedule", r"arrange").search(text)
    direct_recording = spec.target == "recording" and _rx(r"开始录音", r"录下来", r"录制", r"start\s+recording", r"record\s+this").search(text)
    if not (verb or desire or direct_booking or direct_recording):
        return None
    action_verb = "start" if spec.target == "recording" and direct_recording else "open"
    return SemanticRoute(
        primary_intent=spec.intent,  # type: ignore[arg-type]
        domain="interaction",
        action_mode="execute",
        confidence=1.0,
        actions=[_action(action_verb, spec.target, request.text)],
        entities={"language": request.language},
        risk="business_state" if spec.target in {"meeting_booking", "contact_registration", "recording"} else "low",
        reason_codes=["deterministic_interaction_grammar"],
        source="fast_path",
        complexity="not_applicable",
        answer_engine="realtime",
    )


def _office_action_route(request: SemanticRouteRequest, text: str, specs: list[TargetSpec]) -> SemanticRoute | None:
    office_specs = [spec for spec in specs if spec.domain == "office"]
    if len(office_specs) != 1 or len(specs) != 1:
        return None
    if _NEGATION.search(text) or _EXPLANATION.search(text) or _HYPOTHETICAL.search(text) or _QUOTATION.search(text):
        return None
    spec = office_specs[0]
    verb = _verb_for_target(text, spec.target)
    if verb is None or not _remainder_is_filler(text, specs, verb):
        return None
    return SemanticRoute(
        primary_intent=spec.intent,  # type: ignore[arg-type]
        domain="office",
        action_mode="execute",
        confidence=1.0,
        actions=[_action(verb, spec.target, request.text)],
        entities={"language": request.language},
        risk="low",
        reason_codes=["deterministic_single_office_action_grammar"],
        source="fast_path",
        complexity="not_applicable",
        answer_engine="office_interpreter",
    )


def _ambiguous_action_route(request: SemanticRouteRequest, text: str, specs: list[TargetSpec]) -> SemanticRoute | None:
    has_action = bool(_OPEN.search(text) or _CLOSE.search(text) or _PLAY.search(text) or _STOP.search(text))
    if not has_action:
        return None
    if len(specs) == 0 and _AMBIGUOUS_PRONOUN.search(text):
        question = (
            "请明确要操作的应用或界面，例如 Teams、OneNote、PowerPoint、预约日历或登记表。"
            if request.language == "zh"
            else "Please name the application or panel, such as Teams, OneNote, PowerPoint, the booking calendar, or registration form."
        )
    elif len(specs) > 1:
        question = (
            "我听到了多个目标。请一次说明一个操作，或明确它们的执行顺序。"
            if request.language == "zh"
            else "I heard multiple targets. Please state one action at a time or specify the execution order."
        )
    else:
        return None
    return SemanticRoute(
        primary_intent="unknown",
        domain="unknown",
        action_mode="clarify",
        confidence=1.0,
        requires_clarification=True,
        clarification_question=question,
        entities={"language": request.language, "targets": [spec.target for spec in specs]},
        risk="none",
        reason_codes=["deterministic_ambiguous_action_grammar"],
        source="fast_path",
        complexity="not_applicable",
        answer_engine="backend",
    )


def classify_deterministic(request: SemanticRouteRequest) -> SemanticRoute | None:
    """High-coverage deterministic grammar for common exhibition language.

    This is deliberately broader than an exact-phrase fast path, but it remains
    bounded by target, action, negation and speech-act checks. It never maps an
    explanation, hypothetical, quotation or negated action to execution.
    """

    text = _normalise(request.text)
    if not text:
        return None
    for classifier in (_identity_route, _volume_route, _slide_route):
        route = classifier(request, text)
        if route is not None:
            return route

    specs = _targets(text)
    for classifier in (
        _negative_or_discussion_route,
        _interaction_route,
        _office_action_route,
        _ambiguous_action_route,
    ):
        route = classifier(request, text, specs)
        if route is not None:
            return route
    return None
