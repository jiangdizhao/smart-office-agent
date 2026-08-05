from __future__ import annotations

import re

from app.semantic_route_models import SemanticAction, SemanticRoute


_OPEN = re.compile(r"(?:打开|开启|启动|运行|调出|进入|显示|open\b|launch\b|start\b|show\b|display\b)", re.IGNORECASE)
_CLOSE = re.compile(r"(?:关闭|关掉|退出|结束|收起|close\b|quit\b|exit\b|turn\s+off|shut\s+down)", re.IGNORECASE)
_PLAY = re.compile(r"(?:播放|放一首|放点|来一首|play\b)", re.IGNORECASE)
_STOP = re.compile(r"(?:停止|停止播放|别放了|关音乐|stop\b)", re.IGNORECASE)
_NEGATION = re.compile(r"(?:不要|先别|别|不用|无需|不需要|请勿|do\s+not|don't|dont|not\s+now|never)", re.IGNORECASE)
_TARGET_PATTERNS = {
    "teams": re.compile(r"(?:microsoft\s*)?teams|微软团队", re.IGNORECASE),
    "onenote": re.compile(r"one\s*note|onenote|微软笔记|数字笔记", re.IGNORECASE),
    "powerpoint": re.compile(r"power\s*point|powerpoint|ppt|幻灯片|演示文稿", re.IGNORECASE),
    "music": re.compile(r"music|song|歌曲|音乐|媒体播放器|media\s*player", re.IGNORECASE),
}


def _normalise(value: str) -> str:
    return re.sub(r"[\s，。！？、;；:：,.!?]+", "", str(value or "").casefold())


def _evidence_present(text: str, action: SemanticAction) -> bool:
    evidence = _normalise(action.evidence)
    source = _normalise(text)
    return bool(evidence and evidence in source)


def _verb_consistent(text: str, action: SemanticAction) -> bool:
    source = str(text or "")
    verb = str(action.verb or "").casefold()
    has_open = bool(_OPEN.search(source))
    has_close = bool(_CLOSE.search(source))
    has_play = bool(_PLAY.search(source))
    has_stop = bool(_STOP.search(source))
    if verb == "open" and has_close and not has_open:
        return False
    if verb == "close" and has_open and not has_close:
        return False
    if verb == "start" and has_stop and not (has_play or has_open):
        return False
    if verb == "stop" and (has_play or has_open) and not (has_stop or has_close):
        return False
    return True


def _affirmed_action_is_explicitly_negated(text: str, action: SemanticAction) -> bool:
    if action.polarity != "affirmed" or not _NEGATION.search(text):
        return False
    target = _TARGET_PATTERNS.get(str(action.target or "").casefold())
    if target is None:
        return False
    pattern = re.compile(
        rf"(?:{_NEGATION.pattern})[^。！？!?]{{0,32}}(?:{target.pattern})",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def validate_semantic_action_evidence(
    route: SemanticRoute,
    text: str,
) -> SemanticRoute:
    """Remove actions that lack evidence or contradict explicit user wording."""

    actions: list[SemanticAction] = []
    evidence_rejected = 0
    verb_rejected = 0
    negation_rejected = 0
    for action in route.actions:
        if not _evidence_present(text, action):
            evidence_rejected += 1
            continue
        if not _verb_consistent(text, action):
            verb_rejected += 1
            continue
        if _affirmed_action_is_explicitly_negated(text, action):
            negation_rejected += 1
            continue
        actions.append(action)

    negated = [
        action for action in route.negated_actions if _evidence_present(text, action)
    ]
    removed_negated = len(route.negated_actions) - len(negated)
    if not evidence_rejected and not verb_rejected and not negation_rejected and not removed_negated:
        return route

    reasons = list(route.reason_codes)
    if evidence_rejected:
        reasons.append(f"action_evidence_rejected:{evidence_rejected}")
    if verb_rejected:
        reasons.append(f"action_verb_conflict_rejected:{verb_rejected}")
    if negation_rejected:
        reasons.append(f"affirmed_action_negation_rejected:{negation_rejected}")
    if removed_negated:
        reasons.append(f"negated_action_evidence_rejected:{removed_negated}")
    return route.model_copy(
        update={
            "actions": actions,
            "negated_actions": negated,
            "reason_codes": reasons[:20],
        }
    )
