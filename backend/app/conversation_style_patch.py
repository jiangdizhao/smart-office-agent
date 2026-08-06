from __future__ import annotations

from app import general_chat_api

_original_instructions = general_chat_api._instructions


def _balanced_instructions(language: general_chat_api.Language) -> str:
    text = _original_instructions(language)
    if language == "en":
        return text.replace(
            "Every response must contain at least one noticeable human-like element: a short natural reaction, a playful phrase, a small change in vocal energy, an expressive pause, or a subtle smiling delivery. Use no more than one full joke.",
            "Use a noticeable human-like element only occasionally, roughly every second reply. Alternate short particles, natural reactions, and small human observations according to context; do not force a textual opening, repeat the previous opening style, or use more than one full joke.",
        )
    return text.replace(
        "每次回答必须至少包含一个明显的“活人感”元素：短反应、轻松表达、一次自然语气变化、短停顿或带轻微笑意的表达。每次最多一个完整笑话。",
        "大约每两次回答使用一次明显的“活人感”元素即可，不要每次都强行添加文字开场。应结合内容在短语气词、自然反应和有人情味的小观察之间交替，不能连续使用相同开场方式；每次最多一个完整笑话。",
    )


general_chat_api._instructions = _balanced_instructions
