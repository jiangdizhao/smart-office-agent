from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

from app.sales_config import sales_config
from app.sales_models import (
    HumourDirective,
    Language,
    SalesFeatureFlags,
    SalesReplyPlan,
    SalesSessionState,
)

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    clean = raw.strip().casefold()
    if clean in _TRUE_VALUES:
        return True
    if clean in _FALSE_VALUES:
        return False
    return default


@dataclass(frozen=True)
class DemonstrationDecision:
    capability_id: str
    capability_status: str
    action: str
    onsite_allowed: bool
    reason: str
    recommended_next_action: str | None
    approved_pitch: str | None
    prohibited_claims: tuple[str, ...]


class SalesRuntimePolicy:
    """Hard policy gates for the active Phase 1 sales runtime."""

    def feature_flags(self) -> SalesFeatureFlags:
        # Phase 1 is the approved default for the exhibition branch. Every feature
        # remains independently disableable through an explicit environment value.
        agent_enabled = _env_bool("SMART_OFFICE_SALES_AGENT_ENABLED", True)
        realtime_mode = os.getenv("SMART_OFFICE_REALTIME_MODE", "quality").strip().casefold()
        if realtime_mode not in {"quality", "economy"}:
            realtime_mode = "quality"
        return SalesFeatureFlags(
            agent_enabled=agent_enabled,
            proactive_enabled=(
                agent_enabled and _env_bool("SMART_OFFICE_SALES_PROACTIVE_ENABLED", True)
            ),
            humour_enabled=(
                agent_enabled and _env_bool("SMART_OFFICE_SALES_HUMOUR_ENABLED", True)
            ),
            profile_persistence_enabled=(
                agent_enabled
                and _env_bool(
                    "SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED",
                    True,
                )
            ),
            telemetry_enabled=_env_bool("SMART_OFFICE_SALES_TELEMETRY_ENABLED", True),
            realtime_mode=realtime_mode,  # type: ignore[arg-type]
        )

    def status(self) -> dict[str, Any]:
        flags = self.feature_flags()
        requested = {
            "agent_enabled": _env_bool("SMART_OFFICE_SALES_AGENT_ENABLED", True),
            "proactive_enabled": _env_bool(
                "SMART_OFFICE_SALES_PROACTIVE_ENABLED", True
            ),
            "humour_enabled": _env_bool("SMART_OFFICE_SALES_HUMOUR_ENABLED", True),
            "profile_persistence_enabled": _env_bool(
                "SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED", True
            ),
            "telemetry_enabled": _env_bool(
                "SMART_OFFICE_SALES_TELEMETRY_ENABLED", True
            ),
        }
        suppressed = [
            name
            for name in (
                "proactive_enabled",
                "humour_enabled",
                "profile_persistence_enabled",
            )
            if requested[name] and not getattr(flags, name)
        ]
        return {
            "phase": "phase1_sales_runtime",
            "effective_flags": flags.model_dump(mode="json"),
            "requested_flags": requested,
            "suppressed_without_agent": suppressed,
            "runtime_active": flags.agent_enabled,
            "default_behaviour_unchanged": not flags.agent_enabled,
            "quality_baseline": "gpt-realtime-2.1",
        }

    def demonstration_decision(
        self,
        capability_id: str,
        *,
        language: Language = "zh",
        explicit_demo_request_count: int = 1,
    ) -> DemonstrationDecision:
        capability = sales_config.capability(capability_id)
        if capability is None:
            return DemonstrationDecision(
                capability_id=capability_id,
                capability_status="unknown",
                action="do_not_claim_capability",
                onsite_allowed=False,
                reason="Capability is not present in the approved catalog.",
                recommended_next_action=None,
                approved_pitch=None,
                prohibited_claims=("do_not_invent_unknown_capability",),
            )

        status = capability.status
        repeated = explicit_demo_request_count >= 2
        immediate_conversion = capability.category == "conversion" and status == "live_demo"
        onsite_allowed = bool(
            immediate_conversion
            or (
                status == "appointment_demo"
                and repeated
                and capability.onsite_fallback_after_repeated_request
                and capability.live_actions
            )
        )
        if immediate_conversion:
            action = capability.recommended_next_action
            reason = "Booking and registration are immediate conversion actions."
        elif status == "appointment_demo" and not repeated:
            action = "offer_booking"
            reason = "The first demonstration request follows the appointment-first policy."
        elif status == "appointment_demo" and onsite_allowed:
            action = capability.onsite_fallback_after_repeated_request or "allowlisted_demo"
            reason = "The visitor repeated the request; only the allowlisted short demo is permitted."
        elif status == "custom_integration":
            action = "discover_and_offer_booking"
            reason = "Custom integration requires the customer's real workflow context."
        elif status == "roadmap":
            action = "describe_as_future_direction"
            reason = "Roadmap capabilities cannot be described as implemented."
        elif status == "restricted":
            action = "state_boundary"
            reason = "Restricted capabilities must remain within the approved boundary."
        else:
            action = capability.recommended_next_action
            reason = "The approved capability policy applies."

        return DemonstrationDecision(
            capability_id=capability.capability_id,
            capability_status=status,
            action=action,
            onsite_allowed=onsite_allowed,
            reason=reason,
            recommended_next_action=capability.recommended_next_action,
            approved_pitch=capability.approved_pitch.model_dump().get(language),
            prohibited_claims=tuple(capability.prohibited_claims),
        )

    @staticmethod
    def can_offer_booking(state: SalesSessionState) -> bool:
        gap_ok = (
            state.last_booking_offer_turn is None
            or state.turn_count - state.last_booking_offer_turn >= 2
        )
        return (
            state.stage != "close"
            and not state.booking_rejected
            and not state.booking_opened
            and state.booking_offer_count < 2
            and gap_ok
        )

    @staticmethod
    def can_offer_contact(state: SalesSessionState) -> bool:
        return (
            state.stage != "close"
            and not state.contact_rejected
            and not state.contact_opened
            and state.contact_offer_count < 1
            and state.effective_user_turn_count >= 2
            and state.value_delivered
        )

    @staticmethod
    def can_proactively_nudge(
        state: SalesSessionState,
        *,
        user_speaking: bool,
        agent_speaking: bool,
        tool_active: bool,
        interaction_input_active: bool,
    ) -> bool:
        return bool(
            state.stage != "close"
            and not state.disengaged
            and state.proactive_nudge_count < 2
            and not user_speaking
            and not agent_speaking
            and not tool_active
            and not interaction_input_active
        )

    @staticmethod
    def _stable_index(seed: str, size: int) -> int:
        if size <= 1:
            return 0
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % size

    def approved_humour_text(
        self,
        *,
        theme: str,
        language: Language,
        seed: str,
    ) -> str | None:
        bundle = sales_config.load()
        if bundle is None:
            return None
        entry = next(
            (
                item
                for item in bundle.claims.humour_themes
                if str(item.get("theme_id") or "") == theme
            ),
            None,
        )
        if not isinstance(entry, dict):
            return None
        examples = entry.get(f"examples_{language}")
        if not isinstance(examples, list):
            return None
        cleaned = [" ".join(str(item).strip().split()) for item in examples if str(item).strip()]
        if not cleaned:
            return None
        return cleaned[self._stable_index(f"{seed}|{theme}|{language}", len(cleaned))]

    def humour_directive(
        self,
        state: SalesSessionState,
        *,
        context: str,
        theme: str | None,
        language: Language = "zh",
        seed: str = "",
    ) -> HumourDirective:
        flags = self.feature_flags()
        bundle = sales_config.load()
        if not flags.humour_enabled or bundle is None:
            return HumourDirective(reason="humour_feature_disabled")

        persona_humour = bundle.persona.humour
        forbidden = set(persona_humour.get("forbidden_contexts", []))
        allowed_contexts = set(persona_humour.get("allowed_contexts", []))
        theme_entry = next(
            (
                item
                for item in bundle.claims.humour_themes
                if isinstance(item, dict) and str(item.get("theme_id") or "") == str(theme or "")
            ),
            None,
        )
        minimum_gap = int(persona_humour.get("minimum_turn_gap", 4))

        if context in forbidden:
            return HumourDirective(reason=f"forbidden_context:{context}")
        if context not in allowed_contexts:
            return HumourDirective(reason=f"unapproved_context:{context}")
        if not theme or not isinstance(theme_entry, dict):
            return HumourDirective(reason="unknown_or_missing_theme")
        theme_contexts = set(theme_entry.get("allowed_contexts", []))
        if context not in theme_contexts:
            return HumourDirective(reason="theme_not_approved_for_context")
        if theme in state.humour_themes_used:
            return HumourDirective(reason="theme_already_used_in_visit")
        if state.turns_since_humour < minimum_gap:
            return HumourDirective(reason="minimum_turn_gap_not_met")
        text = self.approved_humour_text(
            theme=theme,
            language=language,
            seed=seed or f"{state.conversation_id}|{state.visit_id}|{state.turn_count}",
        )
        if not text:
            return HumourDirective(reason="approved_theme_has_no_text")

        return HumourDirective(
            allowed=True,
            intensity="light",
            theme=theme,
            text=text,
            reason="approved_low_risk_context",
            maximum_lines=1,
            forbidden_topics=sorted(forbidden),
        )

    def approved_cost_claim(
        self,
        *,
        language: Language,
        seed: str,
    ) -> tuple[str, str] | None:
        bundle = sales_config.load()
        if bundle is None:
            return None
        claim = next(
            (
                item
                for item in bundle.claims.claims
                if item.get("claim_id") == "typical_ai_voice_cost_analogy"
                and item.get("enabled") is True
            ),
            None,
        )
        if not isinstance(claim, dict):
            return None
        examples = claim.get("examples", {}).get(language, [])
        examples = [" ".join(str(item).strip().split()) for item in examples if str(item).strip()]
        follow_up = " ".join(
            str(claim.get("required_follow_up", {}).get(language, "")).strip().split()
        )
        if not examples or not follow_up:
            return None
        selected = examples[self._stable_index(f"{seed}|cost|{language}", len(examples))]
        return selected, follow_up

    def phase0_reply_plan(
        self,
        state: SalesSessionState,
        *,
        language: Language,
        goal: str,
        capability_ids: list[str] | None = None,
        suggested_question: str | None = None,
    ) -> SalesReplyPlan:
        # Kept as a compatibility helper for the original foundation contract.
        approved_claims: list[str] = []
        prohibited_claims = ["never_claim_unverified_execution"]
        for capability_id in capability_ids or []:
            capability = sales_config.capability(capability_id)
            if capability is None:
                prohibited_claims.append(f"do_not_invent:{capability_id}")
                continue
            approved_claims.extend(capability.customer_value.get(language, []))
            prohibited_claims.extend(capability.prohibited_claims)

        context = [
            f"{key}: {value}" for key, value in sorted(state.explicit_facts.items())
        ]
        context.extend(f"pain_point: {item}" for item in state.pain_points)
        return SalesReplyPlan(
            conversation_id=state.conversation_id,
            visit_id=state.visit_id,
            language=language,
            reply_mode="pure_sales",
            sales_stage=state.stage,
            goal=goal,
            approved_claims=approved_claims,
            prohibited_claims=sorted(set(prohibited_claims)),
            visitor_context=context,
            capability_ids=capability_ids or [],
            suggested_question=suggested_question,
            recommended_action=None,
            maximum_sentences=4,
        )


sales_runtime_policy = SalesRuntimePolicy()
