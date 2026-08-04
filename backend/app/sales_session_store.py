from __future__ import annotations

import hashlib
from threading import RLock
from typing import Iterable

from app.sales_models import (
    ALLOWED_PROFILE_FIELDS,
    Language,
    SalesSessionPatch,
    SalesSessionState,
    SalesStage,
    utc_now_iso,
)

_ALLOWED_STAGE_TRANSITIONS: dict[SalesStage, set[SalesStage]] = {
    "attract": {"discover", "recommend", "demonstrate", "handle_objection", "convert", "close"},
    "discover": {"recommend", "demonstrate", "handle_objection", "convert", "close"},
    "recommend": {"discover", "demonstrate", "handle_objection", "convert", "close"},
    "demonstrate": {"discover", "recommend", "handle_objection", "convert", "close"},
    "handle_objection": {"discover", "recommend", "demonstrate", "convert", "close"},
    "convert": {"recommend", "close"},
    "close": set(),
}


def _clean_list(values: Iterable[str], maximum: int = 20) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").strip().split())[:300]
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= maximum:
            break
    return result


class SalesSessionStore:
    """In-memory, Visit-fenced sales state.

    Anonymous sales context is never written by this store. The optional Phase 1
    persistence service may copy an explicit summary only after a consented contact
    record exists for the same Visit.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[tuple[str, str], SalesSessionState] = {}

    @staticmethod
    def _key(conversation_id: str, visit_id: str) -> tuple[str, str]:
        conversation = str(conversation_id or "").strip()
        visit = str(visit_id or "").strip()
        if not conversation or not visit:
            raise ValueError("conversation_id and visit_id are required")
        return conversation, visit

    @staticmethod
    def _copy(state: SalesSessionState) -> SalesSessionState:
        return SalesSessionState.model_validate(state.model_dump(mode="python"))

    def get_or_create(
        self,
        conversation_id: str,
        visit_id: str,
        *,
        language: Language = "zh",
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key)
            if state is None:
                state = SalesSessionState(
                    conversation_id=key[0],
                    visit_id=key[1],
                    language=language,
                )
                self._sessions[key] = state
            elif state.language != language:
                state.language = language
                self._sessions[key] = state
            return self._copy(state)

    def snapshot(self, conversation_id: str, visit_id: str) -> SalesSessionState | None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            value = self._sessions.get(key)
            return None if value is None else self._copy(value)

    def _update(self, state: SalesSessionState) -> SalesSessionState:
        state.updated_at = utc_now_iso()
        self._sessions[(state.conversation_id, state.visit_id)] = state
        return self._copy(state)

    def record_user_turn(
        self,
        conversation_id: str,
        visit_id: str,
        *,
        effective: bool = True,
        language: Language = "zh",
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
                language=language,
            )
            state.language = language
            state.turn_count += 1
            if effective:
                state.effective_user_turn_count += 1
            state.turns_since_humour += 1
            return self._update(state)

    def apply_explicit_patch(
        self,
        conversation_id: str,
        visit_id: str,
        patch: SalesSessionPatch,
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            for field, value in patch.explicit_facts.items():
                if field in state.declined_fields:
                    continue
                state.explicit_facts[field] = value
            state.pain_points = _clean_list([*state.pain_points, *patch.pain_points])
            state.interested_capabilities = _clean_list(
                [*state.interested_capabilities, *patch.interested_capabilities]
            )
            state.objections = _clean_list([*state.objections, *patch.objections])
            return self._update(state)

    def transition(
        self,
        conversation_id: str,
        visit_id: str,
        stage: SalesStage,
        *,
        action: str | None = None,
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            if stage != state.stage and stage not in _ALLOWED_STAGE_TRANSITIONS[state.stage]:
                raise ValueError(
                    f"Invalid sales-stage transition: {state.stage} -> {stage}"
                )
            state.stage = stage
            if action:
                state.last_sales_action = " ".join(action.strip().split())[:160]
            if stage == "close":
                state.disengaged = True
            return self._update(state)

    def mark_field_asked(
        self,
        conversation_id: str,
        visit_id: str,
        field: str,
    ) -> tuple[bool, SalesSessionState]:
        clean = str(field or "").strip()
        if clean not in ALLOWED_PROFILE_FIELDS:
            raise ValueError(f"Unsupported discovery field: {clean}")
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            allowed = (
                clean not in state.asked_fields
                and clean not in state.declined_fields
                and (
                    state.last_profile_question_turn is None
                    or state.turn_count - state.last_profile_question_turn >= 1
                )
            )
            if allowed:
                state.asked_fields.append(clean)
                state.last_profile_question_turn = state.turn_count
            return allowed, self._update(state)

    def decline_field(
        self,
        conversation_id: str,
        visit_id: str,
        field: str,
    ) -> SalesSessionState:
        clean = str(field or "").strip()
        if clean not in ALLOWED_PROFILE_FIELDS:
            raise ValueError(f"Unsupported discovery field: {clean}")
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            if clean not in state.declined_fields:
                state.declined_fields.append(clean)
            state.explicit_facts.pop(clean, None)
            return self._update(state)

    def increment_demo_request(
        self,
        conversation_id: str,
        visit_id: str,
        capability_id: str,
    ) -> tuple[int, SalesSessionState]:
        clean = str(capability_id or "").strip()
        if not clean:
            raise ValueError("capability_id is required")
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            count = min(10, int(state.explicit_demo_request_counts.get(clean, 0)) + 1)
            state.explicit_demo_request_counts[clean] = count
            state.last_recommended_capability = clean
            state.last_sales_action = "explicit_demo_requested"
            return count, self._update(state)

    def offer_booking(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> tuple[bool, SalesSessionState]:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            gap_ok = (
                state.last_booking_offer_turn is None
                or state.turn_count - state.last_booking_offer_turn >= 2
            )
            allowed = (
                not state.booking_rejected
                and not state.booking_opened
                and state.booking_offer_count < 2
                and gap_ok
            )
            if allowed:
                state.booking_offer_count += 1
                state.last_booking_offer_turn = state.turn_count
                state.last_sales_action = "booking_offered"
            return allowed, self._update(state)

    def reject_booking(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            state.booking_rejected = True
            state.last_sales_action = "booking_rejected"
            return self._update(state)

    def mark_booking_opened(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            if state.last_sales_action == "booking_requested":
                state.last_sales_action = "booking_panel_requested"
            else:
                state.booking_opened = True
                state.last_sales_action = "booking_opened"
            return self._update(state)

    def offer_contact(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> tuple[bool, SalesSessionState]:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            allowed = (
                not state.contact_rejected
                and not state.contact_opened
                and state.contact_offer_count < 1
                and state.effective_user_turn_count >= 2
                and state.value_delivered
            )
            if allowed:
                state.contact_offer_count += 1
                state.last_sales_action = "contact_offered"
            return allowed, self._update(state)

    def reject_contact(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            state.contact_rejected = True
            state.last_sales_action = "contact_rejected"
            return self._update(state)

    def mark_contact_opened(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            if state.last_sales_action == "contact_registration_requested":
                state.last_sales_action = "contact_panel_requested"
            else:
                state.contact_opened = True
                state.last_sales_action = "contact_opened"
            return self._update(state)

    def mark_value_delivered(
        self,
        conversation_id: str,
        visit_id: str,
        *,
        action: str = "customer_value_explained",
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            state.value_delivered = True
            state.last_sales_action = action[:160]
            return self._update(state)

    def record_cost_claim(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> tuple[bool, SalesSessionState]:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            allowed = state.cost_claim_used_count < 1
            if allowed:
                state.cost_claim_used_count = 1
                state.last_sales_action = "approved_cost_analogy_used"
            return allowed, self._update(state)

    def record_demonstration(
        self,
        conversation_id: str,
        visit_id: str,
        capability_id: str,
    ) -> SalesSessionState:
        clean = str(capability_id or "").strip()
        if not clean:
            raise ValueError("capability_id is required")
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            if clean not in state.demonstrated_capabilities:
                state.demonstrated_capabilities.append(clean)
            state.last_recommended_capability = clean
            state.last_sales_action = "onsite_demo_recorded"
            return self._update(state)

    def record_humour(
        self,
        conversation_id: str,
        visit_id: str,
        *,
        theme: str,
        text: str,
        minimum_turn_gap: int = 4,
    ) -> tuple[bool, SalesSessionState]:
        clean_theme = str(theme or "").strip()
        clean_text = " ".join(str(text or "").strip().split())
        if not clean_theme or not clean_text:
            raise ValueError("Humour theme and text are required")
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            text_hash = hashlib.sha256(clean_text.casefold().encode("utf-8")).hexdigest()
            allowed = (
                clean_theme not in state.humour_themes_used
                and state.turns_since_humour >= max(1, minimum_turn_gap)
                and text_hash != state.last_humour_text_hash
            )
            if allowed:
                state.humour_used_count += 1
                state.humour_themes_used.append(clean_theme)
                state.last_humour_theme = clean_theme
                state.last_humour_text_hash = text_hash
                state.turns_since_humour = 0
                state.last_sales_action = "humour_used"
            return allowed, self._update(state)

    def record_proactive_nudge(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> tuple[bool, SalesSessionState]:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            allowed = state.proactive_nudge_count < 2 and state.stage != "close"
            if allowed:
                state.proactive_nudge_count += 1
                state.last_sales_action = "proactive_nudge"
            return allowed, self._update(state)

    def mark_profile_persisted(
        self,
        conversation_id: str,
        visit_id: str,
    ) -> SalesSessionState:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            state = self._sessions.get(key) or SalesSessionState(
                conversation_id=key[0],
                visit_id=key[1],
            )
            state.profile_persisted = True
            state.last_sales_action = "consented_sales_profile_persisted"
            return self._update(state)

    def end_visit(self, conversation_id: str, visit_id: str) -> SalesSessionState | None:
        key = self._key(conversation_id, visit_id)
        with self._lock:
            value = self._sessions.pop(key, None)
            return None if value is None else self._copy(value)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def status(self) -> dict[str, int | str]:
        with self._lock:
            return {
                "schema_version": "sales-session-v1",
                "phase": "phase1_sales_runtime",
                "active_session_count": len(self._sessions),
            }


sales_session_store = SalesSessionStore()
