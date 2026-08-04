from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import deque
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from app.openai_services import generate_response_text, general_chat_model, parse_json_object
from app.semantic_pending_store import semantic_pending_intents
from app.semantic_route_models import (
    PendingIntent,
    SemanticAction,
    SemanticEvidence,
    SemanticProfileExtraction,
    SemanticRoute,
    SemanticRouteRequest,
)
from app.semantic_route_policy import semantic_route_policy

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "semantic_router.json"
_ALLOWED_PROFILE_FIELDS = {
    "industry",
    "role",
    "company_type",
    "office_pain_points",
    "interested_capabilities",
    "name",
}
_FORBIDDEN_PROFILE_FIELDS = {
    "age",
    "gender",
    "ethnicity",
    "income",
    "budget",
    "authority",
    "emotion",
    "health",
    "religion",
    "politics",
}


def _normalise(text: str) -> str:
    return (
        str(text or "")
        .normalize("NFKC") if hasattr(str(text or ""), "normalize") else str(text or "")
    )


def _clean(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _fold(text: str) -> str:
    return _clean(text).casefold()


def _contains_evidence(source: str, evidence: str) -> bool:
    source_folded = re.sub(r"\s+", "", source.casefold())
    evidence_folded = re.sub(r"\s+", "", evidence.casefold())
    return bool(evidence_folded and evidence_folded in source_folded)


def _safe_clarification(language: str, reason: str) -> SemanticRoute:
    text = (
        "我还不能安全确定您是要执行操作、回应刚才的问题，还是只想了解功能。请明确说明您希望我做什么。"
        if language == "zh"
        else "I cannot safely determine whether you want an action, are responding to the previous question, or only want an explanation. Please state what you want me to do."
    )
    return SemanticRoute(
        primary_intent="unknown",
        domain="unknown",
        action_mode="clarify",
        confidence=0.0,
        requires_clarification=True,
        clarification_question=text,
        entities={"language": language},
        risk="none",
        reason_codes=[reason],
        source="safe_fallback",
        complexity="not_applicable",
        answer_engine="backend",
    )


class UnifiedSemanticRouter:
    """One natural-language understanding entry point for the exhibition UI.

    The fast path deliberately covers only exact, low-ambiguity utterances. All open
    language is interpreted by a schema-constrained model. The result is then
    evidence-checked and passed through the deterministic policy engine before any
    caller may execute an action.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._recent: deque[dict[str, Any]] = deque(maxlen=100)
        self._config: dict[str, Any] | None = None

    def config(self) -> dict[str, Any]:
        with self._lock:
            if self._config is None:
                self._config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
                pending = self._config.get("pending_intent", {})
                semantic_pending_intents.configure(
                    maximum_age_seconds=float(pending.get("maximum_age_seconds", 180))
                )
                maximum = int(
                    self._config.get("diagnostics", {}).get(
                        "maximum_recent_decisions", 100
                    )
                )
                self._recent = deque(self._recent, maxlen=max(10, min(500, maximum)))
            return dict(self._config)

    @staticmethod
    def _action(
        verb: str,
        target: str,
        evidence: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> SemanticAction:
        return SemanticAction(
            verb=verb,  # type: ignore[arg-type]
            target=target,
            arguments=dict(arguments or {}),
            polarity="affirmed",
            speech_act="command",
            evidence=evidence,
        )

    def _fast_path(self, request: SemanticRouteRequest) -> SemanticRoute | None:
        clean = _fold(request.text).strip(" ，。！？!?.,")
        language = request.language
        entities = {"language": language}

        identity = {
            "你是谁",
            "请介绍一下自己",
            "介绍一下自己",
            "自我介绍",
            "who are you",
            "introduce yourself",
        }
        if clean in identity:
            return SemanticRoute(
                primary_intent="self_introduction",
                domain="identity",
                action_mode="answer_only",
                confidence=1.0,
                entities=entities,
                risk="none",
                reason_codes=["exact_identity_fast_path"],
                source="fast_path",
                complexity="simple",
                answer_engine="realtime",
            )

        exact_actions: dict[str, tuple[str, str, str]] = {
            "打开 teams": ("open", "teams", "application_action"),
            "打开teams": ("open", "teams", "application_action"),
            "open teams": ("open", "teams", "application_action"),
            "关闭 teams": ("close", "teams", "application_action"),
            "关闭teams": ("close", "teams", "application_action"),
            "close teams": ("close", "teams", "application_action"),
            "打开 onenote": ("open", "onenote", "application_action"),
            "打开onenote": ("open", "onenote", "application_action"),
            "open onenote": ("open", "onenote", "application_action"),
            "关闭 onenote": ("close", "onenote", "application_action"),
            "关闭onenote": ("close", "onenote", "application_action"),
            "close onenote": ("close", "onenote", "application_action"),
            "播放音乐": ("start", "music", "system_action"),
            "随机播放一首音乐": ("start", "music", "system_action"),
            "play music": ("start", "music", "system_action"),
            "停止音乐": ("stop", "music", "system_action"),
            "关闭音乐": ("stop", "music", "system_action"),
            "stop music": ("stop", "music", "system_action"),
            "打开预约日历": ("open", "meeting_booking", "open_meeting_booking"),
            "打开会议预约": ("open", "meeting_booking", "open_meeting_booking"),
            "book a meeting": ("open", "meeting_booking", "open_meeting_booking"),
            "打开登记信息表": ("open", "contact_registration", "open_contact_registration"),
            "打开登记表": ("open", "contact_registration", "open_contact_registration"),
            "open contact registration": ("open", "contact_registration", "open_contact_registration"),
            "打开结果中心": ("open", "result_center", "open_result_center"),
            "open result center": ("open", "result_center", "open_result_center"),
            "打开对话总结": ("open", "transcript", "open_transcript"),
            "打开对话记录": ("open", "transcript", "open_transcript"),
            "open conversation summary": ("open", "transcript", "open_transcript"),
            "开始录音": ("start", "recording", "open_recording"),
            "打开实时录音": ("open", "recording", "open_recording"),
            "start recording": ("start", "recording", "open_recording"),
        }
        exact = exact_actions.get(clean)
        if exact:
            verb, target, intent = exact
            domain = "interaction" if intent.startswith("open_") else "office"
            risk = semantic_route_policy.risk_for_target(target)
            return SemanticRoute(
                primary_intent=intent,  # type: ignore[arg-type]
                domain=domain,  # type: ignore[arg-type]
                action_mode="execute",
                confidence=1.0,
                actions=[self._action(verb, target, request.text)],
                entities=entities,
                risk=risk,
                reason_codes=["exact_single_action_fast_path"],
                source="fast_path",
                complexity="not_applicable",
                answer_engine="office_interpreter" if domain == "office" else "realtime",
            )

        slide_actions = {
            "下一页": ("next", "powerpoint"),
            "下一张": ("next", "powerpoint"),
            "next slide": ("next", "powerpoint"),
            "上一页": ("previous", "powerpoint"),
            "上一张": ("previous", "powerpoint"),
            "previous slide": ("previous", "powerpoint"),
        }
        slide = slide_actions.get(clean)
        if slide:
            return SemanticRoute(
                primary_intent="presentation_action",
                domain="office",
                action_mode="execute",
                confidence=1.0,
                actions=[self._action(slide[0], slide[1], request.text)],
                entities=entities,
                risk="low",
                reason_codes=["exact_presentation_fast_path"],
                source="fast_path",
                answer_engine="office_interpreter",
            )

        volume = re.fullmatch(
            r"(?:把)?(?:系统)?音量(?:调到|设置为|设为)\s*(\d{1,3})\s*%?",
            clean,
        ) or re.fullmatch(r"set (?:the )?volume to (\d{1,3})\s*%?", clean)
        if volume:
            percent = int(volume.group(1))
            if 0 <= percent <= 100:
                return SemanticRoute(
                    primary_intent="system_action",
                    domain="office",
                    action_mode="execute",
                    confidence=1.0,
                    actions=[
                        self._action(
                            "set",
                            "system_volume",
                            request.text,
                            arguments={"percent": percent},
                        )
                    ],
                    entities={**entities, "percent": percent},
                    risk="low",
                    reason_codes=["bounded_numeric_volume_fast_path"],
                    source="fast_path",
                    answer_engine="office_interpreter",
                )
        return None

    @staticmethod
    def _brief_response(text: str) -> str | None:
        clean = _fold(text).strip(" ，。！？!?.,")
        affirm = {"可以", "好", "好的", "行", "愿意", "没问题", "yes", "sure", "okay", "ok", "go ahead"}
        reject = {"不", "不用", "不用了", "不了", "不需要", "算了", "no", "no thanks", "not now"}
        if clean in affirm:
            return "accept"
        if clean in reject:
            return "reject"
        return None

    def _pending_route(
        self,
        request: SemanticRouteRequest,
        pending: PendingIntent | None,
    ) -> SemanticRoute | None:
        if pending is None:
            return None
        response = self._brief_response(request.text)
        if response is None:
            return None
        entities = {"language": request.language, "pending_intent": pending.intent_type}
        if pending.intent_type == "booking_offer":
            return SemanticRoute(
                primary_intent="booking_response",
                domain="sales",
                action_mode="delegate",
                confidence=1.0,
                entities={**entities, "conversion_response": response},
                sales_signals=[f"booking_{response}"],
                risk="business_state" if response == "accept" else "none",
                reason_codes=["structured_pending_booking_response"],
                profile_extraction=SemanticProfileExtraction(
                    booking_intent="accept" if response == "accept" else "reject",
                    brief_affirmation=response == "accept",
                    brief_rejection=response == "reject",
                    sales_relevant=True,
                ),
                source="pending_intent",
                answer_engine="backend",
            )
        if pending.intent_type == "contact_offer":
            return SemanticRoute(
                primary_intent="contact_response",
                domain="sales",
                action_mode="delegate",
                confidence=1.0,
                entities={**entities, "conversion_response": response},
                sales_signals=[f"contact_{response}"],
                risk="business_state" if response == "accept" else "none",
                reason_codes=["structured_pending_contact_response"],
                profile_extraction=SemanticProfileExtraction(
                    contact_intent="accept" if response == "accept" else "reject",
                    brief_affirmation=response == "accept",
                    brief_rejection=response == "reject",
                    sales_relevant=True,
                ),
                source="pending_intent",
                answer_engine="backend",
            )
        return None

    @staticmethod
    def _instructions(language: str) -> str:
        return f"""
You are the unified semantic router for a bilingual Smart Office exhibition agent. Analyse only the user's communicative intent. Never claim that an action was executed and never invent a tool name.
Return exactly one JSON object matching semantic-route-v1. Use these domains only: office, interaction, sales, general, identity, privacy, unknown. Use these action modes only: execute, answer_only, clarify, request_confirmation, reject, delegate.
Distinguish commands from questions, explanation requests, hypothetical conditions, quotations and negated actions. A sentence that discusses, compares or asks about an application is not an execution command. Put explicitly negated actions in negated_actions, never in actions.
Use execute only for a clear explicit action. Use delegate for an Office or sales request that needs a domain planner. Use answer_only for identity, capability explanation or general questions. Use clarify whenever the target, action, reference or polarity is unclear.
Allowed primary intents: self_introduction, capability_explanation, general_question, conversation_continue, conversation_end, office_action, presentation_action, email_action, system_action, application_action, multi_action_workflow, open_contact_registration, open_meeting_booking, open_recording, open_transcript, open_result_center, close_interaction_panel, provide_profile_context, express_pain_point, request_recommendation, request_demo, booking_response, contact_response, sales_objection, cost_question, privacy_question, unknown.
Allowed action targets: teams, onenote, music, powerpoint, system_volume, contact_registration, meeting_booking, recording, transcript, result_center, email_draft, email_send, self_introduction, capability_explanation. Other Office targets may be described in entities but must use action_mode=delegate, not execute.
Every extracted sales profile value must contain an exact evidence substring from the current user message. Allowed explicit_facts keys are industry, role, company_type, office_pain_points, interested_capabilities and name. Never infer age, gender, ethnicity, income, budget, authority, emotion, health, religion or politics. Do not infer a fact from appearance, tone or context.
For a request such as 'do not open Teams, explain it', output an answer-only capability explanation plus a negated Teams action. For 'Teams why does it not open', output a question/explanation request, not execute. For 'if you opened Teams', use hypothetical and do not execute. For quoted commands, do not execute.
Set entities.language to '{language}'. complexity is simple, complex or not_applicable. answer_engine is realtime for concise general/identity answers, terra for genuinely detailed analysis, office_interpreter for delegated Office work, and backend for sales/policy handling.
The JSON must include every SemanticRoute field, including profile_extraction with all fields. Do not include Markdown.
""".strip()

    @staticmethod
    def _input(request: SemanticRouteRequest) -> str:
        history = "\n".join(
            f"{turn.role.upper()}: {turn.text}" for turn in request.recent_turns[-8:]
        ) or "(none)"
        return (
            f"Language: {request.language}\nActor: {request.actor_type}\n"
            f"Runtime context: {request.runtime_context.model_dump_json()}\n"
            f"Recent turns:\n{history}\n\nCurrent user message:\n{request.text}"
        )

    def _validate_profile_evidence(
        self,
        route: SemanticRoute,
        text: str,
    ) -> SemanticRoute:
        profile = route.profile_extraction
        facts: dict[str, SemanticEvidence] = {}
        for field, item in profile.explicit_facts.items():
            if field not in _ALLOWED_PROFILE_FIELDS or field in _FORBIDDEN_PROFILE_FIELDS:
                continue
            if _contains_evidence(text, item.evidence):
                facts[field] = item
        pain = [item for item in profile.pain_points if _contains_evidence(text, item.evidence)]
        capabilities = [
            item
            for item in profile.interested_capabilities
            if _contains_evidence(text, item.evidence)
        ]
        objections = [
            item for item in profile.objections if _contains_evidence(text, item.evidence)
        ]
        declined = [
            field for field in profile.declined_fields if field in _ALLOWED_PROFILE_FIELDS
        ]
        validated = profile.model_copy(
            update={
                "explicit_facts": facts,
                "pain_points": pain,
                "interested_capabilities": capabilities,
                "objections": objections,
                "declined_fields": declined,
            }
        )
        if validated != profile:
            return route.model_copy(
                update={
                    "profile_extraction": validated,
                    "reason_codes": [
                        *route.reason_codes,
                        "profile_evidence_deterministically_validated",
                    ][:20],
                }
            )
        return route

    async def classify(
        self,
        request: SemanticRouteRequest,
    ) -> tuple[SemanticRoute, str | None, str | None]:
        self.config()
        fast = self._fast_path(request)
        if fast is not None:
            return fast, None, None

        pending: PendingIntent | None = None
        if request.visit_id:
            pending = semantic_pending_intents.peek(
                request.conversation_id, request.visit_id
            )
        pending_route = self._pending_route(request, pending)
        if pending_route is not None:
            if request.visit_id:
                semantic_pending_intents.consume(
                    request.conversation_id, request.visit_id
                )
            return pending_route, None, pending.intent_type if pending else None

        model = os.getenv("OPENAI_SEMANTIC_ROUTER_MODEL", "").strip() or general_chat_model()
        try:
            text, selected_model = await generate_response_text(
                input_text=self._input(request),
                instructions=self._instructions(request.language),
                model=model,
                max_output_tokens=int(
                    self.config().get("model", {}).get("maximum_output_tokens", 1800)
                ),
            )
            candidate = parse_json_object(text)
            candidate["source"] = "semantic_model"
            candidate.setdefault("entities", {})["language"] = request.language
            route = SemanticRoute.model_validate(candidate)
            route = self._validate_profile_evidence(route, request.text)
            if pending is not None and request.visit_id:
                semantic_pending_intents.consume(
                    request.conversation_id, request.visit_id
                )
            return route, selected_model, None
        except Exception as exc:
            return _safe_clarification(
                request.language,
                f"semantic_model_unavailable_or_invalid:{type(exc).__name__}",
            ), model, None

    def record(
        self,
        *,
        request: SemanticRouteRequest,
        route: SemanticRoute,
        final_decision: str,
        decision_id: str,
        model: str | None,
        elapsed_ms: int,
        pending_intent_used: str | None,
        legacy_comparison: dict[str, Any] | None,
    ) -> None:
        store_raw = bool(self.config().get("diagnostics", {}).get("store_raw_text"))
        payload = {
            "decision_id": decision_id,
            "conversation_id": request.conversation_id,
            "visit_id": request.visit_id,
            "text": request.text if store_raw else None,
            "text_hash": hashlib.sha256(request.text.encode("utf-8")).hexdigest(),
            "primary_intent": route.primary_intent,
            "domain": route.domain,
            "source": route.source,
            "confidence": route.confidence,
            "router_action_mode": route.action_mode,
            "final_policy_decision": final_decision,
            "risk": route.risk,
            "actions": [action.model_dump(mode="json") for action in route.actions],
            "negated_actions": [
                action.model_dump(mode="json") for action in route.negated_actions
            ],
            "reason_codes": route.reason_codes,
            "pending_intent_used": pending_intent_used,
            "legacy_comparison": legacy_comparison,
            "model": model,
            "elapsed_ms": elapsed_ms,
            "recorded_at_epoch": time.time(),
        }
        with self._lock:
            self._recent.append(payload)

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._recent))

    def status(self) -> dict[str, Any]:
        return {
            "configuration": self.config(),
            "policy": semantic_route_policy.status(),
            "pending": semantic_pending_intents.status(),
            "recent_decision_count": len(self._recent),
        }

    def new_decision_id(self) -> str:
        return uuid4().hex


unified_semantic_router = UnifiedSemanticRouter()
