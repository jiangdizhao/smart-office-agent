from __future__ import annotations

import asyncio
import json
import os
from functools import partial
from pathlib import Path
from typing import Any, Literal

from app.current_slide_insight import _shape_content
from app.models import ToolResult
from app.office_artifacts import generate_presentation_summary
from app.openai_services import generate_response_text
from app.presentation_config import presentation_config

Language = Literal["zh", "en"]
_MAX_PROMPT_CHARACTERS = 28_000


def _clean(value: Any, maximum: int = 500) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


def _extract_deck(source_path: Path) -> list[dict[str, Any]]:
    from pptx import Presentation

    deck = Presentation(str(source_path))
    records: list[dict[str, Any]] = []
    for slide_number, slide in enumerate(deck.slides, start=1):
        title, text_blocks, table_blocks, chart_blocks, notes = _shape_content(slide)
        records.append(
            {
                "slide_number": slide_number,
                "title": title,
                "text": text_blocks[:8],
                "tables": table_blocks[:3],
                "charts": chart_blocks[:3],
                "notes": notes,
            }
        )
    return records


def _prompt_payload(records: list[dict[str, Any]]) -> str:
    compact: list[dict[str, Any]] = []
    used = 0
    for record in records:
        item = {
            "slide_number": record["slide_number"],
            "title": _clean(record.get("title"), 220),
            "text": [_clean(value, 360) for value in record.get("text", [])[:6]],
            "tables": [_clean(value, 500) for value in record.get("tables", [])[:2]],
            "charts": [_clean(value, 220) for value in record.get("charts", [])[:2]],
            "notes": _clean(record.get("notes"), 600),
        }
        encoded = json.dumps(item, ensure_ascii=False)
        if compact and used + len(encoded) > _MAX_PROMPT_CHARACTERS:
            compact.append(
                {
                    "slide_number": record["slide_number"],
                    "title": item["title"],
                    "content_omitted_due_to_prompt_limit": True,
                }
            )
            continue
        compact.append(item)
        used += len(encoded)
    return json.dumps({"slides": compact}, ensure_ascii=False, indent=2)


def _fallback_text(records: list[dict[str, Any]], language: Language) -> str:
    titles = [
        _clean(record.get("title"), 100)
        for record in records
        if _clean(record.get("title"), 100)
    ]
    evidence: list[str] = []
    for record in records:
        for value in [
            *record.get("text", []),
            *record.get("tables", []),
            *record.get("charts", []),
        ]:
            clean = _clean(value, 180)
            if clean and clean not in evidence:
                evidence.append(clean)
            if len(evidence) >= 4:
                break
        if len(evidence) >= 4:
            break

    if language == "en":
        if not titles and not evidence:
            return (
                f"The presentation contains {len(records)} slides, but no readable text, table, "
                "chart label or speaker note was available for a reliable content summary."
            )
        topic_text = "; ".join(titles[:6]) or "the extracted presentation content"
        detail_text = "; ".join(evidence[:4])
        suffix = f" Supporting points include {detail_text}." if detail_text else ""
        return (
            f"The presentation contains {len(records)} slides and mainly covers {topic_text}."
            f"{suffix} This is a deterministic fallback because the language model summary was unavailable."
        )

    if not titles and not evidence:
        return f"这份演示共 {len(records)} 页，但没有提取到可读正文、表格、图表标签或备注，因此暂时无法可靠概括其内容。"
    topic_text = "、".join(titles[:6]) or "已提取的演示内容"
    detail_text = "；".join(evidence[:4])
    suffix = f" 支撑要点包括：{detail_text}。" if detail_text else ""
    return (
        f"这份演示共 {len(records)} 页，主要围绕{topic_text}展开。"
        f"{suffix}当前使用的是确定性备用摘要，因为语言模型摘要暂时不可用。"
    )


def _instructions(language: Language) -> str:
    if language == "en":
        return """
You summarize the complete PowerPoint deck supplied as structured slide data.
Produce one coherent spoken executive summary of the entire deck, not a slide-by-slide transcript.
State the overall purpose, the main themes, the strongest supporting points, and the practical takeaway.
Use only facts present in the supplied slide data. Do not invent missing visual details, numbers, customers, outcomes or recommendations.
Do not mention JSON, extraction, prompts, tools or model limitations.
Use approximately 120 to 190 words in natural English. Return only the final summary as plain text.
""".strip()
    return """
你负责根据提供的结构化幻灯片内容，总结整份 PowerPoint 演示。
输出一段连贯、可直接朗读的执行摘要，而不是逐页复述或拼接原文。
应概括整份演示的总体目的、主要主题、最有代表性的支撑要点和实际结论。
只能使用幻灯片数据中明确存在的事实，不得编造缺失的视觉细节、数字、客户、效果或建议。
不要提到 JSON、内容提取、提示词、工具或模型限制。
控制在约 220 至 360 个中文字符，语言自然、专业。只返回最终摘要正文。
""".strip()


async def whole_presentation_insight(
    *,
    language: Language = "zh",
) -> ToolResult:
    tool_name = "presentation_summarize_whole_deck"
    source_path = presentation_config.presentation_path.resolve()
    if not source_path.is_file():
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=(
                f"Configured presentation was not found: {source_path}"
            ),
            data={
                "execution_mode": "failed",
                "requested_state": {"whole_presentation_summary": True},
                "presentation_path": str(source_path),
            },
        )

    try:
        records = await asyncio.to_thread(_extract_deck, source_path)
        if not records:
            raise RuntimeError("The configured presentation has no slides.")

        artifact_result = await asyncio.to_thread(
            partial(generate_presentation_summary, language=language)
        )
        model = (
            os.getenv("SMART_OFFICE_PRESENTATION_SUMMARY_MODEL", "").strip()
            or None
        )
        summary_mode = "llm"
        summary_model = ""
        summary_error = ""
        try:
            spoken, summary_model = await generate_response_text(
                input_text=_prompt_payload(records),
                instructions=_instructions(language),
                model=model,
                max_output_tokens=900,
            )
            spoken = _clean(spoken, 2_400)
            if not spoken:
                raise RuntimeError("The language model returned an empty presentation summary.")
        except Exception as exc:
            summary_mode = "deterministic_fallback"
            summary_error = str(exc)
            spoken = _fallback_text(records, language)

        artifacts = list(artifact_result.artifacts) if artifact_result.ok else []
        data = {
            "execution_mode": "real",
            "requested_state": {"whole_presentation_summary": True},
            "whole_presentation_summary": True,
            "language": language,
            "slide_count": len(records),
            "presentation_path": str(source_path),
            "spoken_insight": spoken,
            "summary_mode": summary_mode,
            "summary_model": summary_model or None,
            "summary_error": summary_error or None,
            "artifact_created": artifact_result.ok,
            "artifact_result": artifact_result.model_dump(mode="json"),
        }
        if artifact_result.ok:
            data.update(
                {
                    "summary_path": artifact_result.data.get("summary_path"),
                    "summary_path_relative": artifact_result.data.get("summary_path_relative"),
                    "artifact_url": artifact_result.data.get("artifact_url"),
                }
            )
        return ToolResult(
            tool_name=tool_name,
            ok=True,
            message=spoken,
            artifacts=artifacts,
            data=data,
            raw={
                "summary_mode": summary_mode,
                "summary_model": summary_model or None,
                "summary_error": summary_error or None,
            },
        )
    except Exception as exc:
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            message=f"The complete presentation could not be summarized: {exc}",
            data={
                "execution_mode": "failed",
                "requested_state": {"whole_presentation_summary": True},
                "presentation_path": str(source_path),
                "error": str(exc),
            },
        )
