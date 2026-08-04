from __future__ import annotations

import re

from app.semantic_route_models import SemanticAction, SemanticRoute


def _normalise(value: str) -> str:
    return re.sub(r"[\s，。！？、;；:：,.!?]+", "", str(value or "").casefold())


def _evidence_present(text: str, action: SemanticAction) -> bool:
    evidence = _normalise(action.evidence)
    source = _normalise(text)
    return bool(evidence and evidence in source)


def validate_semantic_action_evidence(
    route: SemanticRoute,
    text: str,
) -> SemanticRoute:
    """Remove actions whose claimed evidence is absent from the current utterance.

    Fast-path actions also carry the original user text as evidence, so every path
    follows the same invariant. A route that loses its only executable action will
    be converted to clarification by the deterministic policy engine.
    """

    actions = [action for action in route.actions if _evidence_present(text, action)]
    negated = [
        action for action in route.negated_actions if _evidence_present(text, action)
    ]
    removed_actions = len(route.actions) - len(actions)
    removed_negated = len(route.negated_actions) - len(negated)
    if not removed_actions and not removed_negated:
        return route
    reasons = list(route.reason_codes)
    if removed_actions:
        reasons.append(f"action_evidence_rejected:{removed_actions}")
    if removed_negated:
        reasons.append(f"negated_action_evidence_rejected:{removed_negated}")
    return route.model_copy(
        update={
            "actions": actions,
            "negated_actions": negated,
            "reason_codes": reasons[:20],
        }
    )
