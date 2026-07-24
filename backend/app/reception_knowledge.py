from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Literal

Language = Literal["zh", "en"]

_MATCH_SEPARATOR_PATTERN = re.compile(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()\-]+")
_SELF_INTRO_MARKERS = (
    "介绍一下你自己",
    "介绍下你自己",
    "介绍你自己",
    "介绍一下自己",
    "介绍下自己",
    "介绍一下您自己",
    "介绍您自己",
    "你是谁",
    "您是谁",
    "你能做什么",
    "您能做什么",
    "你可以做什么",
    "您可以做什么",
    "你会做什么",
    "introduceyourself",
    "tellmeaboutyourself",
    "whoareyou",
    "whatcanyoudo",
)
_SOLUTION_MARKERS = (
    "介绍一下smartoffice",
    "介绍smartoffice",
    "smartoffice方案",
    "smartoffice解决方案",
    "介绍一下你们公司",
    "介绍一下公司",
    "你们公司做什么",
    "你们是做什么的",
    "tellmeaboutsmartoffice",
    "introducethesmartofficesolution",
    "whatdoesyourcompanydo",
)
_CAPABILITY_MARKERS = (
    "核心能力",
    "主要功能",
    "有哪些功能",
    "能做哪些事情",
    "功能介绍",
    "corecapabilities",
    "mainfeatures",
    "whatfeatures",
)
_DEPLOYMENT_MARKERS = (
    "适合什么场景",
    "应用场景",
    "部署场景",
    "可以用在哪里",
    "usescenarios",
    "deploymentscenarios",
    "wherecanitbeused",
)


def _compact(value: str) -> str:
    return _MATCH_SEPARATOR_PATTERN.sub("", value.casefold())


def _contains_marker(compact_query: str, markers: tuple[str, ...]) -> bool:
    return any(marker in compact_query for marker in markers)


@dataclass(frozen=True)
class KnowledgeEntry:
    entry_id: str
    category: str
    title: dict[str, str]
    keywords: dict[str, list[str]]
    answer: dict[str, str]
    public: bool


@dataclass(frozen=True)
class KnowledgeMatch:
    entry: KnowledgeEntry
    answer: str
    source_id: str
    content_version: str
    updated_at: str


class ReceptionKnowledgeService:
    def __init__(self, source_path: Path | None = None) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self._source_path = source_path or (
            repo_root / "data" / "company_knowledge" / "company_profile.json"
        )
        self._lock = RLock()
        self._loaded = False
        self._entries: list[KnowledgeEntry] = []
        self._content_version = "unknown"
        self._updated_at = "unknown"
        self._disclaimer: dict[str, str] = {}

    def _load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            payload = json.loads(self._source_path.read_text(encoding="utf-8"))
            self._content_version = str(payload.get("content_version", "unknown"))
            self._updated_at = str(payload.get("updated_at", "unknown"))
            self._disclaimer = dict(payload.get("disclaimer", {}))
            self._entries = [
                KnowledgeEntry(
                    entry_id=str(item["id"]),
                    category=str(item.get("category", "general")),
                    title=dict(item.get("title", {})),
                    keywords={
                        "zh": list(item.get("keywords", {}).get("zh", [])),
                        "en": list(item.get("keywords", {}).get("en", [])),
                    },
                    answer=dict(item.get("answer", {})),
                    public=bool(item.get("public", False)),
                )
                for item in payload.get("entries", [])
            ]
            self._loaded = True

    @property
    def source_path(self) -> Path:
        return self._source_path

    def status(self) -> dict:
        self._load()
        return {
            "ok": True,
            "configured": self._source_path.exists(),
            "source_path": str(self._source_path),
            "content_version": self._content_version,
            "updated_at": self._updated_at,
            "entry_count": len(self._entries),
            "mode": "approved_local_content",
        }

    def list_public(self) -> list[KnowledgeEntry]:
        self._load()
        return [entry for entry in self._entries if entry.public]

    def get_entry(self, entry_id: str) -> KnowledgeEntry | None:
        self._load()
        return next((entry for entry in self._entries if entry.entry_id == entry_id), None)

    def disclaimer(self, language: Language) -> str:
        self._load()
        return self._disclaimer.get(language, self._disclaimer.get("en", ""))

    def _intent_boost(self, entry: KnowledgeEntry, compact_query: str) -> int:
        if entry.entry_id == "assistant_identity" and _contains_marker(
            compact_query, _SELF_INTRO_MARKERS
        ):
            return 50
        if entry.entry_id == "solution_overview" and _contains_marker(
            compact_query, _SOLUTION_MARKERS
        ):
            return 45
        if entry.entry_id == "core_capabilities" and _contains_marker(
            compact_query, _CAPABILITY_MARKERS
        ):
            return 40
        if entry.entry_id == "deployment_scenarios" and _contains_marker(
            compact_query, _DEPLOYMENT_MARKERS
        ):
            return 40
        return 0

    def search(self, query: str, language: Language) -> KnowledgeMatch:
        self._load()
        lowered = query.casefold()
        compact_query = _compact(query)
        candidates = [entry for entry in self._entries if entry.public]

        def score(entry: KnowledgeEntry) -> tuple[int, int]:
            keywords = entry.keywords.get(language, []) + entry.keywords.get("en", [])
            keyword_score = 0
            for keyword in keywords:
                lowered_keyword = keyword.casefold()
                compact_keyword = _compact(keyword)
                if lowered_keyword and lowered_keyword in lowered:
                    keyword_score += 3
                elif compact_keyword and compact_keyword in compact_query:
                    keyword_score += 2
            title = entry.title.get(language, entry.title.get("en", "")).casefold()
            compact_title = _compact(title)
            title_score = 2 if title and title in lowered else 1 if compact_title in compact_query else 0
            return (
                self._intent_boost(entry, compact_query) + keyword_score + title_score,
                -len(entry.entry_id),
            )

        best = max(candidates, key=score, default=None)
        if best is None:
            raise RuntimeError("Reception knowledge source has no public entries.")

        best_score = score(best)[0]
        if best_score <= 0:
            best = self.get_entry("public_information_boundary") or best

        answer = best.answer.get(language, best.answer.get("en", ""))
        return KnowledgeMatch(
            entry=best,
            answer=answer,
            source_id=f"company_profile:{best.entry_id}",
            content_version=self._content_version,
            updated_at=self._updated_at,
        )


reception_knowledge = ReceptionKnowledgeService()
