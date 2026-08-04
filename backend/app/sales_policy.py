from __future__ import annotations

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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in _TRUE_VALUES


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
    """Hard policy gates for the Phase 0 sales foundation.

    These methods do not alter the current conversation runtime. Phase 1 will call
    them from the active router. Their behaviour is already contract-tested so the
    user-facing implementation cannot bypass Visit limits later.
    """

    def feature_flags(self) -> SalesFeatureFlags:
        agent_enabled = _env_bool("SMART_OFFICE_SALES_AGENT_ENABLED", False)
        realtime_mode = os.getenv("SMART_OFFICE_REALTIME_MODE", "quality").strip().casefold()
        if realtime_mode not in {"quality", "economy"}:
            realtime_mode = "quality"
        return SalesFeatureFlags(
            agent_enabled=agent_enabled,
            proactive_enabled=(
                agent_enabled and _env_bool("SMART_OFFICE_SALES_PROACTIVE_ENABLED", False)
            ),
            humour_enabled=(
                agent_enabled and _env_bool("SMART_OFFICE_SALES_HUMOUR_ENABLED", False)
            ),
            profile_persistence_enabled=(
                agent_enabled
                and _env_bool(
                    "SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED",
                    False,
                )
            ),
            telemetry_enabled=_env_bool("SMART_OFFICE_SALES_TELEMETRY_ENABLED", True),
            realtime_mode=realtime_mode,  # type: ignore[arg-type]
        )

    def status(self) -> dict[str, Any]:
        flags = self.feature_flags()
        requested = {
            "agent_enabled": _env_bool("SMART_OFFICE_SALES_AGENT_ENABLED", False),
            "proactive_enabled": _env_bool(
                "SMART_OFFICE_SALES_PROACTIVE_ENABLED", False
            ),
            "humour_enabled": _env_bool("SMART_OFFICE_SALES_HUMOUR_ENABLED", False),
            "profile_persistence_enabled": _env_bool(
                "SMART_OFFICE_SALES_PROFILE_PERSISTENCE_ENABLED", False
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
            "phase": "phase0_sales_foundation",
            "effective_flags": flags.model_dump(mode="json"),
            "requested_flags": requested,
            "suppressed_without_agent": suppressed,
            "default_behaviour_unchanged": not flags.agent_enabled,
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
            and state.booking_offer_count < 2
            and gap_ok
        )

    @staticmethod
    def can_offer_contact(state: SalesSessionState) -> bool:
        return (
            state.stage != "close"
            and not state.contact_rejected
            and state.contact_offer_count < 1
            and state.effective_user_turn_count >= 2
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
            and state.proactive_nudge_count < 2
            and not user_speaking
            and not agent_speaking
            and not tool_active
            and not interaction_input_active
        )

    def humour_directive(
        self,
        state: SalesSessionState,
        *,
        context: str,
        theme: str | None,
    ) -> HumourDirective:
        flags = self.feature_flags()
        bundle = sales_config.load()
        if not flags.humour_enabled or bundle is None:
            return HumourDirective(reason="humour_feature_disabled")

        persona_humour = bundle.persona.humour
        forbidden = set(persona_humour.get("forbidden_contexts", []))
        allowed_contexts = set(persona_humour.get("allowed_contexts", []))
        known_themes = {
            str(item.get("theme_id"))
            for item in bundle.claims.humour_themes
            if isinstance(item, dict) and item.get("theme_id")
        }
        minimum_gap = int(persona_humour.get("minimum_turn_gap", 4))

        if context in forbidden:
            return HumourDirective(reason=f"forbidden_context:{context}")
        if context not in allowed_contexts:
            return HumourDirective(reason=f"unapproved_context:{context}")
        if not theme or theme not in known_themes:
            return HumourDirective(reason="unknown_or_missing_theme")
        if theme in state.humour_themes_used:
            return HumourDirective(reason="theme_already_used_in_visit")
        if state.turns_since_humour < minimum_gap:
            return HumourDirective(reason="minimum_turn_gap_not_met")

        return HumourDirective(
            allowed=True,
            intensity="light",
            theme=theme,
            reason="approved_low_risk_context",
            maximum_lines=1,
            forbidden_topics=sorted(forbidden),
        )

    def phase0_reply_plan(
        self,
        state: SalesSessionState,
        *,
        language: Language,
        goal: str,
        capability_ids: list[str] | None = None,
        suggested_question: str | None = None,
    ) -> SalesReplyPlan:
        """Build a non-executing contract sample used by Phase 0 tests and review.

        Phase 1 will add the real planner. This method deliberately contains no LLM
        call and never asserts that a tool action was completed.
        """

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
