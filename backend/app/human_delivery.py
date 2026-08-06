from __future__ import annotations

import hashlib
import re
from typing import Literal

Language = Literal["zh", "en"]

_PRICE_ZH = re.compile(r"价格|报价|多少钱|费用|成本|贵不贵|便宜|折扣|优惠")
_PRICE_EN = re.compile(r"\b(?:price|pricing|quote|cost|how much|expensive|cheap|discount)\b", re.I)
_DISCOUNT = re.compile(r"折扣|优惠|\bdiscounts?\b", re.I)
_EXPENSIVE = re.compile(r"贵不贵|贵吗|太贵|很贵|\b(?:expensive|too costly|too much)\b", re.I)
_PRIVACY = re.compile(r"隐私|个人信息|人脸|身份|录音|数据安全|privacy|personal information|face data|identity|data security", re.I)
_ACTION = re.compile(r"打开|关闭|启动|停止|播放|发送|创建|调整|设置|执行|演示|放映|open|close|launch|start|stop|play|send|create|adjust|set|execute|present", re.I)
_FAILURE = re.compile(r"没有成功|未成功|失败|未完成|没有完成|无法确认|did not complete|did not open|failed|not completed|could not verify", re.I)
_UNKNOWN = re.compile(r"不知道|不清楚|没有这方面的资料|资料中没有|无法回答|不确定|i don'?t know|not sure|no information|cannot answer|can'?t answer", re.I)
_UNAVAILABLE = re.compile(r"不能执行|无法执行|暂时不能|目前不能|尚未接入|没有权限|功能未开放|not connected|not available|cannot perform|can'?t perform|not enabled|not implemented", re.I)
_GENERIC_OPENING = re.compile(
    r"^(?:嗯哼[，,]?我来看看|哦[，,]?我来看看|嗯[，,]?我来看看|"
    r"好[，,]?我大概抓到重点了|我抓到重点了|"
    r"Mm-hm,? let me see|Right,? I think I have the picture)[。.!！]?\s*",
    re.I,
)


def _pick(values: tuple[str, ...], seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return values[int.from_bytes(digest[:4], "big") % len(values)]


def _price_reply(user_text: str, language: Language) -> str:
    if language == "en":
        if _DISCOUNT.search(user_text):
            return "Ah, now we're negotiating. You secure the best price, and I'll prove I'm worth it; specific discounts depend on the project size and solution scope. Would you like the lightweight version or the complete solution?"
        if _EXPENSIVE.search(user_text):
            return "Well, it helps to look at it another way: this is not simply buying several screens, but deploying a digital team that keeps working. The formal price depends on the hardware, connected functions and deployment scope. Would you like the basic version or the complete solution?"
        options = (
            "Ah, the question every boss eventually asks. Personally, I can work all day for the price of a few coffees; the formal price depends on the number of screens, connected functions and deployment scope. Would you like the basic version or the complete solution?",
            "Well, I'm not very demanding—give me power and Wi-Fi and I'll happily work all day. The full system price depends on the hardware, agent functions and installation scope. Which configuration would you like me to outline?",
            "Oh, I'm personally very affordable, and I don't even take lunch breaks. The formal quote depends on whether you need a lightweight agent or a complete multi-screen smart office. Which one should we discuss?",
        )
        return _pick(options, user_text)

    if _DISCOUNT.search(user_text):
        return "啊，现在进入谈价环节了。您负责争取好价格，我负责证明自己值得；具体优惠要根据项目规模和方案范围确认。您想先了解轻量版还是完整方案？"
    if _EXPENSIVE.search(user_text):
        return "嗯，这个问题要换个角度看：它不只是买几块屏幕，而是在部署一个可以持续工作的数字团队。正式价格取决于硬件、接入功能和部署范围。您想了解基础版还是完整方案？"
    options = (
        "啊，终于问到老板最关心的问题了。按我的个人开销算，几杯咖啡就能让我工作一整天；正式价格要看屏幕数量、需要接入的功能和部署范围。您想了解基础版还是完整方案？",
        "哦，我的胃口其实不大，有电、有网络，我就愿意一直上班。整套系统的价格会根据硬件配置、Agent 功能和安装范围来确定。您更想了解哪一种配置？",
        "嗯哼，我本人很便宜，甚至不需要午休。不过正式报价要看您是想要轻量版 Agent，还是整套多屏智能办公室。我们先聊哪一种？",
    )
    return _pick(options, user_text)


def ensure_human_like_reply(*, user_text: str, answer: str, language: Language) -> str:
    clean_user = " ".join(user_text.split())
    clean_answer = _GENERIC_OPENING.sub("", " ".join(answer.split())).strip()

    if (_PRICE_ZH if language == "zh" else _PRICE_EN).search(clean_user):
        return _price_reply(clean_user, language)
    if not clean_answer:
        return (
            "这个问题已经走到我当前资料库的边缘了。它值得一个准确答案，不值得我现场靠气势编一个；我可以把它列为后续确认项，让专业同事接棒。"
            if language == "zh"
            else "This question has reached the edge of my current knowledge base. It deserves an accurate answer rather than one improvised with confidence; I can mark it for specialist follow-up."
        )
    if _PRIVACY.search(clean_user) or _PRIVACY.search(clean_answer):
        return clean_answer
    if _FAILURE.search(clean_answer):
        return (
            f"后台刚才眨了一下眼睛，这次还没有完成。{clean_answer} 我可以再试一次，或者换一种方式。"
            if language == "zh"
            else f"The backend blinked for a moment, so that action has not completed. {clean_answer} I can try again or take a different route."
        )
    if _UNAVAILABLE.search(clean_answer) or (_ACTION.search(clean_user) and _UNKNOWN.search(clean_answer)):
        return (
            "这项技能今天还没装到我的工具箱里，不过我可以先演示最接近的流程，或者完成已经接入的部分。"
            if language == "zh"
            else "That skill has not reached my toolbox yet, but I can demonstrate the closest workflow or complete the part that is already connected."
        )
    if _UNKNOWN.search(clean_answer):
        return (
            "这个问题已经走到我当前资料库的边缘了。它值得一个准确答案，不值得我现场靠气势编一个；我可以把它列为后续确认项，让专业同事接棒。"
            if language == "zh"
            else "This question has reached the edge of my current knowledge base. It deserves an accurate answer rather than one improvised with confidence; I can mark it for specialist follow-up."
        )

    # Normal conversational answers stay unprefixed here. The browser's Visit-level
    # scheduler decorates only every second reply and alternates expression types,
    # preventing two independent layers from repeating the same catchphrase.
    return clean_answer
