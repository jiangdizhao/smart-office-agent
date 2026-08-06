from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from app.semantic_route_models import ActionMode, RiskLevel, SemanticRoute

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "semantic_router.json"


class SemanticRoutePolicy:
    """Validates model output and owns every execution boundary.

    The semantic model may describe intent, entities and evidence. It cannot add a
    tool, lower a risk level, bypass consent, or convert a negated/discussion action
    into execution.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._config: dict[str, Any] | None = None

    def config(self) -> dict[str, Any]:
        with self._lock:
            if self._config is None:
                payload = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
                if payload.get("schema_version") != "semantic-router-config-v1":
                    raise RuntimeError("Unsupported semantic router configuration.")
                self._config = payload
            return dict(self._config)

    def mode(self, environment_value: str | None) -> str:
        configured = str(environment_value or "").strip().casefold()
        if configured in {"legacy", "shadow", "unified"}:
            return configured
        default = str(self.config().get("default_mode") or "unified").casefold()
        return default if default in {"legacy", "shadow", "unified"} else "unified"

    def risk_for_target(self, target: str) -> RiskLevel:
        raw = str(self.config().get("risk_by_target", {}).get(target) or "external_effect")
        return raw if raw in {"none", "low", "business_state", "external_effect"} else "external_effect"  # type: ignore[return-value]

    def _target_allowed(self, route: SemanticRoute, target: str) -> bool:
        allowed = self.config().get("allowed_actions", {})
        mapping = {
            "application_action": "application_action",
            "system_action": "system_action",
            "presentation_action": "presentation_action",
            "open_contact_registration": "interaction_action",
            "open_meeting_booking": "interaction_action",
            "open_recording": "interaction_action",
            "open_transcript": "interaction_action",
            "open_result_center": "interaction_action",
            "self_introduction": "identity_action",
            "capability_explanation": "explanation_action",
        }
        group = mapping.get(route.primary_intent)
        return bool(group and target in allowed.get(group, []))

    def apply(self, route: SemanticRoute) -> tuple[SemanticRoute, ActionMode, list[str]]:
        reasons: list[str] = []
        updates: dict[str, Any] = {}

        executable = []
        for action in route.actions:
            if action.polarity != "affirmed":
                reasons.append("nonaffirmed_action_removed")
                continue
            if action.speech_act not in {"command", "response"}:
                reasons.append(f"noncommand_action_removed:{action.speech_act}")
                continue
            if not self._target_allowed(route, action.target):
                reasons.append(f"target_not_allowlisted:{action.target}")
                continue
            executable.append(action)

        if executable != route.actions:
            updates["actions"] = executable

        highest_risk: RiskLevel = "none"
        rank = {"none": 0, "low": 1, "business_state": 2, "external_effect": 3}
        for action in executable:
            candidate = self.risk_for_target(action.target)
            if rank[candidate] > rank[highest_risk]:
                highest_risk = candidate
        if route.risk != highest_risk:
            reasons.append(f"risk_recomputed:{route.risk}->{highest_risk}")
            updates["risk"] = highest_risk

        thresholds = self.config().get("thresholds", {})
        answer_threshold = float(thresholds.get("answer_only", 0.55))
        delegate_threshold = float(thresholds.get("delegate", 0.72))
        execute_threshold = float(thresholds.get("execute_low_risk", 0.90))
        business_threshold = float(thresholds.get("business_state", 0.94))

        final: ActionMode = route.action_mode
        if route.negated_actions and executable:
            final = "clarify"
            reasons.append("affirmed_and_negated_action_conflict")
        elif route.requires_clarification:
            final = "clarify"
            reasons.append("router_requested_clarification")
        elif route.action_mode == "execute":
            if not executable:
                final = "clarify"
                reasons.append("no_policy_validated_executable_action")
            elif highest_risk == "external_effect":
                final = "request_confirmation"
                reasons.append("external_effect_requires_confirmation")
            elif highest_risk == "business_state" and route.confidence < business_threshold:
                final = "clarify"
                reasons.append("business_state_confidence_below_threshold")
            elif highest_risk == "low" and route.confidence < execute_threshold:
                final = "clarify"
                reasons.append("low_risk_execution_confidence_below_threshold")
        elif route.action_mode == "delegate" and route.confidence < delegate_threshold:
            final = "clarify"
            reasons.append("delegation_confidence_below_threshold")
        elif route.action_mode == "answer_only" and route.confidence < answer_threshold:
            final = "clarify"
            reasons.append("answer_confidence_below_threshold")

        if final == "clarify" and not route.clarification_question:
            language = str(route.entities.get("language") or "zh")
            question = (
                "我还不能安全确定您是要执行操作，还是只想了解功能。请明确告诉我要做什么。"
                if language == "zh"
                else "I cannot safely tell whether you want an action performed or only an explanation. Please state the intended action explicitly."
            )
            updates["clarification_question"] = question
            updates["requires_clarification"] = True

        if final != route.action_mode:
            reasons.append(f"action_mode_overridden:{route.action_mode}->{final}")
            updates["action_mode"] = final

        if reasons:
            updates["reason_codes"] = [*route.reason_codes, *reasons][:20]
        return route.model_copy(update=updates), final, reasons

    def status(self) -> dict[str, Any]:
        config = self.config()
        return {
            "schema_version": config.get("schema_version"),
            "default_mode": config.get("default_mode"),
            "thresholds": config.get("thresholds"),
            "risk_target_count": len(config.get("risk_by_target", {})),
            "allowed_action_groups": sorted(config.get("allowed_actions", {}).keys()),
        }


semantic_route_policy = SemanticRoutePolicy()
