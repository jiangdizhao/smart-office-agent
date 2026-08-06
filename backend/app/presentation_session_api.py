from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.openai_services import generate_response_text

router = APIRouter(prefix="/api/presentation/session", tags=["presentation-session"])

Language = Literal["zh", "en"]
_FALLBACK_ZH = "我们今天时间有限，详细的细节欢迎私下与我们讨论，可以留下您的联系方式，我后续会为您服务。"
_FALLBACK_EN = "We have limited time today. You are welcome to discuss the details with us privately. Please leave your contact details and I will follow up with you."
_COVER_ZH = "接下来由我为您介绍 PPT，当您看完后对我说下一页即可翻页。"
_COVER_EN = "I will now introduce this presentation. When you are ready, say next slide and I will continue."
_SLIDE_HEADING = re.compile(r"第\s*([234])\s*页")
_NARRATION_MARKER = re.compile(r"Agent\s*简短讲解稿", re.IGNORECASE)
_TECH_MARKER = re.compile(r"技术答疑稿")


class PresentationSessionScriptResponse(BaseModel):
    ok: bool = True
    source_path: str
    cover_intro: dict[str, str]
    slides: dict[str, dict[str, str]]


class PresentationQuestionRequest(BaseModel):
    slide_number: int = Field(..., ge=2, le=4)
    question: str = Field(..., min_length=1, max_length=4000)
    language: Language = "zh"


class PresentationQuestionResponse(BaseModel):
    ok: bool = True
    related: bool
    spoken_text: str
    open_registration: bool
    model: str | None = None
    source_path: str


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script_directory() -> Path:
    configured = os.getenv("SMART_OFFICE_PRESENTATION_SCRIPT_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else (_repository_root() / "config" / "presentation_knowledge")


def _script_path() -> Path:
    directory = _script_directory()
    preferred = directory / "MEGA_SMART_展览讲解稿与技术答疑知识库.docx"
    if preferred.is_file():
        return preferred
    matches = sorted(directory.glob("*.docx")) if directory.is_dir() else []
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"No presentation script DOCX was found in {directory}. "
        "Place MEGA_SMART_展览讲解稿与技术答疑知识库.docx in that folder."
    )


def _docx_lines(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{namespace}t")]
        value = "".join(parts).strip()
        if value:
            lines.append(" ".join(value.split()))
    return lines


def _parse_script(path: Path) -> dict[int, dict[str, str]]:
    lines = _docx_lines(path)
    sections: dict[int, list[str]] = {}
    current: int | None = None
    for line in lines:
        heading = _SLIDE_HEADING.search(line)
        if heading:
            current = int(heading.group(1))
            sections.setdefault(current, []).append(line)
            continue
        if current in {2, 3, 4}:
            sections.setdefault(current, []).append(line)

    parsed: dict[int, dict[str, str]] = {}
    for slide_number in (2, 3, 4):
        values = sections.get(slide_number, [])
        if not values:
            raise ValueError(f"The DOCX does not contain a recognizable section for slide {slide_number}.")
        narration_index = next((i for i, value in enumerate(values) if _NARRATION_MARKER.search(value)), -1)
        tech_index = next((i for i, value in enumerate(values) if _TECH_MARKER.search(value)), -1)
        if narration_index < 0 or tech_index <= narration_index:
            raise ValueError(
                f"Slide {slide_number} must contain both 'Agent 简短讲解稿' and '技术答疑稿' markers."
            )
        narration_lines = values[narration_index + 1 : tech_index]
        knowledge_lines = values[tech_index + 1 :]
        narration = "".join(narration_lines).strip()
        knowledge = "\n".join(knowledge_lines).strip()
        if not narration:
            raise ValueError(f"Slide {slide_number} has no narration after the narration marker.")
        if not knowledge:
            raise ValueError(f"Slide {slide_number} has no technical knowledge after the technical marker.")
        parsed[slide_number] = {
            "narration": narration,
            "knowledge": knowledge,
        }
    return parsed


def _load_script() -> tuple[Path, dict[int, dict[str, str]]]:
    path = _script_path()
    return path, _parse_script(path)


def _clean_json_payload(value: str) -> dict:
    clean = value.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        clean = clean[start : end + 1]
    payload = json.loads(clean)
    if not isinstance(payload, dict):
        raise ValueError("Model output was not a JSON object.")
    return payload


def _answer_instructions(language: Language) -> str:
    fallback = _FALLBACK_EN if language == "en" else _FALLBACK_ZH
    if language == "en":
        return f"""
You answer a visitor's question during a guided PowerPoint presentation.
Use only the technical knowledge supplied for the CURRENT slide.
If the knowledge contains a direct answer, answer directly.
If it does not contain a direct answer but contains relevant background, use that background to give the most useful truthful answer and clearly state any project-specific limitation.
Set related=false only when the supplied knowledge is completely unrelated to the question and provides no useful background at all.
Do not invent product compatibility, pricing, schedule, deployment facts or completed capabilities.
When related=false, spoken_text must be exactly: {fallback}
Return only JSON with this schema:
{{"related": true_or_false, "spoken_text": "answer"}}
""".strip()
    return f"""
你正在 PPT 导览过程中回答访客问题。
只能依据“当前页面技术稿”回答。
若技术稿有直接答案，直接回答。
若没有直接答案，但存在相关背景，必须先利用相关背景尽量回答，并清楚说明哪些细节取决于具体设备、协议、部署或项目配置。
只有当技术稿与问题完全不相关、没有任何可利用背景时，才将 related 设为 false。
不得编造兼容性、价格、工期、部署事实或尚未确认的能力。
当 related=false 时，spoken_text 必须逐字等于：{fallback}
只返回以下 JSON，不要输出其他内容：
{{"related": true_or_false, "spoken_text": "回答正文"}}
""".strip()


@router.get("/script", response_model=PresentationSessionScriptResponse)
def presentation_session_script() -> PresentationSessionScriptResponse:
    try:
        path, slides = _load_script()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return PresentationSessionScriptResponse(
        source_path=str(path),
        cover_intro={"zh": _COVER_ZH, "en": _COVER_EN},
        slides={str(number): content for number, content in slides.items()},
    )


@router.post("/answer", response_model=PresentationQuestionResponse)
async def presentation_session_answer(req: PresentationQuestionRequest) -> PresentationQuestionResponse:
    try:
        path, slides = _load_script()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    current = slides[req.slide_number]
    model_name = os.getenv("SMART_OFFICE_PRESENTATION_QA_MODEL", "").strip() or None
    input_text = json.dumps(
        {
            "slide_number": req.slide_number,
            "current_slide_technical_knowledge": current["knowledge"],
            "visitor_question": req.question.strip(),
        },
        ensure_ascii=False,
        indent=2,
    )
    try:
        raw, used_model = await generate_response_text(
            input_text=input_text,
            instructions=_answer_instructions(req.language),
            model=model_name,
            max_output_tokens=900,
        )
        payload = _clean_json_payload(raw)
        related = bool(payload.get("related"))
        spoken_text = str(payload.get("spoken_text") or "").strip()
        if not related:
            spoken_text = _FALLBACK_EN if req.language == "en" else _FALLBACK_ZH
        if related and not spoken_text:
            raise ValueError("The model marked the question related but returned an empty answer.")
        return PresentationQuestionResponse(
            related=related,
            spoken_text=spoken_text,
            open_registration=not related,
            model=used_model,
            source_path=str(path),
        )
    except Exception as exc:
        # Do not improvise when the grounded answer service is unavailable.
        fallback = _FALLBACK_EN if req.language == "en" else _FALLBACK_ZH
        return PresentationQuestionResponse(
            related=False,
            spoken_text=fallback,
            open_registration=True,
            model=None,
            source_path=str(path),
        )
