from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx

_OPENAI_API_URL = "https://api.openai.com/v1"


def _api_key() -> str:
    value = os.getenv("OPENAI_API_KEY", "").strip()
    if not value:
        raise RuntimeError("OPENAI_API_KEY is not configured in the Backend process.")
    return value


def _base_url() -> str:
    return os.getenv("OPENAI_API_BASE_URL", _OPENAI_API_URL).rstrip("/")


def general_chat_model() -> str:
    return os.getenv("OPENAI_GENERAL_CHAT_MODEL", "gpt-5.6-terra").strip() or "gpt-5.6-terra"


def transcription_model() -> str:
    return (
        os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe-diarize").strip()
        or "gpt-4o-transcribe-diarize"
    )


def _timeout_seconds(name: str, default: float) -> float:
    try:
        return max(10.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _extract_response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    return "\n".join(parts).strip()


async def generate_response_text(
    *,
    input_text: str,
    instructions: str,
    model: str | None = None,
    max_output_tokens: int = 1800,
) -> tuple[str, str]:
    selected_model = (model or general_chat_model()).strip()
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    body = {
        "model": selected_model,
        "instructions": instructions,
        "input": input_text,
        "max_output_tokens": max(128, min(8000, max_output_tokens)),
    }
    timeout = httpx.Timeout(_timeout_seconds("OPENAI_TEXT_TIMEOUT_SECONDS", 90.0))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{_base_url()}/responses", headers=headers, json=body)
    if response.status_code >= 400:
        detail = response.text[:1600]
        raise RuntimeError(
            f"OpenAI Responses request failed ({response.status_code}) using {selected_model}: {detail}"
        )
    payload = response.json()
    text = _extract_response_text(payload)
    if not text:
        raise RuntimeError("OpenAI Responses returned no text output.")
    return text, selected_model


def _transcription_text(payload: dict[str, Any]) -> str:
    value = payload.get("text")
    return value.strip() if isinstance(value, str) else ""


def _normalise_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        return result
    for index, segment in enumerate(raw_segments, start=1):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        result.append(
            {
                "id": str(segment.get("id") or f"segment-{index}"),
                "speaker": str(segment.get("speaker") or "Speaker"),
                "start": float(segment.get("start") or 0.0),
                "end": float(segment.get("end") or 0.0),
                "text": text,
            }
        )
    return result


async def _transcribe_once(
    audio_path: Path,
    *,
    model: str,
    diarized: bool,
    language: str | None,
) -> dict[str, Any]:
    media_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    data: dict[str, str] = {
        "model": model,
        "response_format": "diarized_json" if diarized else "json",
    }
    if diarized:
        data["chunking_strategy"] = "auto"
    if language in {"zh", "en"}:
        data["language"] = language

    headers = {"Authorization": f"Bearer {_api_key()}"}
    timeout = httpx.Timeout(_timeout_seconds("OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS", 600.0))
    with audio_path.open("rb") as audio_file:
        files = {"file": (audio_path.name, audio_file, media_type)}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{_base_url()}/audio/transcriptions",
                headers=headers,
                data=data,
                files=files,
            )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI transcription failed ({response.status_code}) using {model}: "
            f"{response.text[:1600]}"
        )
    payload = response.json()
    text = _transcription_text(payload)
    if not text:
        raise RuntimeError(f"OpenAI transcription model {model} returned no transcript.")
    return {
        "model": model,
        "text": text,
        "segments": _normalise_segments(payload),
        "duration": payload.get("duration"),
        "raw": payload,
    }


async def transcribe_human_conversation(
    audio_path: Path,
    *,
    language: str | None = None,
) -> dict[str, Any]:
    primary = transcription_model()
    try:
        return await _transcribe_once(
            audio_path,
            model=primary,
            diarized=primary == "gpt-4o-transcribe-diarize",
            language=language,
        )
    except Exception as primary_error:
        fallback = (
            os.getenv("OPENAI_TRANSCRIPTION_FALLBACK_MODEL", "gpt-4o-transcribe").strip()
            or "gpt-4o-transcribe"
        )
        if fallback == primary:
            raise
        try:
            result = await _transcribe_once(
                audio_path,
                model=fallback,
                diarized=False,
                language=language,
            )
            result["fallback_reason"] = str(primary_error)
            return result
        except Exception as fallback_error:
            raise RuntimeError(
                f"Primary transcription failed: {primary_error}; fallback failed: {fallback_error}"
            ) from fallback_error


def parse_json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines).strip()
    value = json.loads(clean)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value
