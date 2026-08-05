from __future__ import annotations

from app.human_delivery import ensure_human_like_reply
from app.turn_router import classify_turn


def _assert_contains_any(value: str, candidates: tuple[str, ...]) -> None:
    assert any(item in value for item in candidates), value


def main() -> None:
    price_zh = ensure_human_like_reply(
        user_text="这套系统多少钱？",
        answer="目前没有固定价格。",
        language="zh",
    )
    _assert_contains_any(price_zh, ("咖啡", "价格", "报价"))
    assert "不知道" not in price_zh

    price_en = ensure_human_like_reply(
        user_text="How much does this system cost?",
        answer="I do not know.",
        language="en",
    )
    _assert_contains_any(price_en.lower(), ("coffee", "price", "quote"))
    assert "i don't know" not in price_en.lower()

    unknown_zh = ensure_human_like_reply(
        user_text="你们尚未公开的芯片规格是什么？",
        answer="我不知道。",
        language="zh",
    )
    _assert_contains_any(unknown_zh, ("资料库", "准确答案", "专业同事"))
    assert "我不知道" not in unknown_zh

    unavailable_en = ensure_human_like_reply(
        user_text="Open the unsupported accounting system.",
        answer="I can't perform that action because it is not connected.",
        language="en",
    )
    _assert_contains_any(unavailable_en.lower(), ("toolbox", "evolving", "closest workflow"))
    assert "i can't" not in unavailable_en.lower()

    ordinary = ensure_human_like_reply(
        user_text="木星离地球多远？",
        answer="木星和地球之间的距离会随两者轨道位置变化。",
        language="zh",
    )
    assert ordinary != "木星和地球之间的距离会随两者轨道位置变化。"

    ppt = classify_turn("打开 PowerPoint 并开始放映", "operator")
    assert ppt.route in {"office_direct", "office_planned_task"}, ppt

    volume = classify_turn("把系统音量设置到 30%", "operator")
    assert volume.route in {"office_direct", "office_planned_task"}, volume

    print("PASS: mandatory human delivery and deterministic Office routing regression")


if __name__ == "__main__":
    main()
