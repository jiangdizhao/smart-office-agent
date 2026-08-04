from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Language = Literal["zh", "en"]
SalesStage = Literal[
    "attract",
    "discover",
    "recommend",
    "demonstrate",
    "handle_objection",
    "convert",
    "close",
]
CapabilityStatus = Literal[
    "live_demo",
    "appointment_demo",
    "custom_integration",
    "roadmap",
    "restricted",
]
ReplyMode = Literal[
    "pure_sales",
    "hybrid_sales",
    "exact_operational",
    "proactive_sales",
    "closing",
]
HumourIntensity = Literal["none", "light"]

ALLOWED_PROFILE_FIELDS = frozenset(
    {
        "industry",
        "role",
        "company_type",
        "office_pain_points",
        "interested_capabilities",
    }
)
FORBIDDEN_INFERENCE_FIELDS = frozenset(
    {
        "age",
        "nationality",
        "ethnicity",
        "income",
        "budget",
        "personality",
        "emotional_state",
        "purchasing_power",
        "job_seniority",
    }
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class LocalisedText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zh: str = Field(..., min_length=1, max_length=4_000)
    en: str = Field(..., min_length=1, max_length=4_000)


class SalesFeatureFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_enabled: bool = False
    proactive_enabled: bool = False
    humour_enabled: bool = False
    profile_persistence_enabled: bool = False
    telemetry_enabled: bool = True
    realtime_mode: Literal["quality", "economy"] = "quality"

    @model_validator(mode="after")
    def dependent_features_require_agent(self) -> "SalesFeatureFlags":
        if not self.agent_enabled and (
            self.proactive_enabled
            or self.humour_enabled
            or self.profile_persistence_enabled
        ):
            raise ValueError(
                "Sales proactive, humour and profile persistence features require "
                "SMART_OFFICE_SALES_AGENT_ENABLED=true."
            )
        return self


class CapabilityEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    capability_id: str = Field(..., pattern=r"^[a-z0-9_]+$", max_length=120)
    title: LocalisedText
    status: CapabilityStatus
    category: str = Field(..., min_length=1, max_length=120)
    keywords: dict[Language, list[str]]
    customer_value: dict[Language, list[str]]
    live_actions: list[str] = Field(default_factory=list)
    approved_pitch: LocalisedText
    recommended_next_action: str = Field(..., min_length=1, max_length=120)
    prohibited_claims: list[str] = Field(default_factory=list)
    onsite_fallback_after_repeated_request: str | None = Field(
        default=None,
        max_length=160,
    )


class CapabilityCatalog(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = Field(..., min_length=1, max_length=120)
    catalog_version: str = Field(..., min_length=1, max_length=120)
    default_status: CapabilityStatus = "appointment_demo"
    status_definitions: dict[CapabilityStatus, str]
    capabilities: list[CapabilityEntry] = Field(..., min_length=1)

    @model_validator(mode="after")
    def unique_capabilities(self) -> "CapabilityCatalog":
        identifiers = [item.capability_id for item in self.capabilities]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Capability IDs must be unique.")
        return self


class SalesPersonaConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    persona_version: str
    identity: LocalisedText
    mission: LocalisedText
    conversation: dict[str, Any]
    conversion: dict[str, Any]
    demonstration: dict[str, Any]
    proactivity: dict[str, Any]
    humour: dict[str, Any]
    model_policy: dict[str, Any]


class SalesPlaybooksConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    playbook_version: str
    global_rules: dict[str, Any]
    discovery_fields: list[dict[str, Any]]
    playbooks: list[dict[str, Any]]
    objections: list[dict[str, Any]]
    disengagement: dict[str, Any]


class SalesClaimsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    claims_version: str
    claims: list[dict[str, Any]]
    humour_themes: list[dict[str, Any]]
    global_humour_rules: dict[str, Any]


class HumourDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool = False
    intensity: HumourIntensity = "none"
    theme: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="not_selected", max_length=200)
    maximum_lines: int = Field(default=0, ge=0, le=1)
    forbidden_topics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_humour_state(self) -> "HumourDirective":
        if not self.allowed:
            self.intensity = "none"
            self.theme = None
            self.maximum_lines = 0
        elif not self.theme:
            raise ValueError("An allowed humour directive requires a theme.")
        return self


class SalesReplyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sales-reply-plan-v1"] = "sales-reply-plan-v1"
    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    language: Language = "zh"
    reply_mode: ReplyMode
    sales_stage: SalesStage
    goal: str = Field(..., min_length=1, max_length=240)
    verified_facts: list[str] = Field(default_factory=list)
    approved_claims: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    visitor_context: list[str] = Field(default_factory=list)
    capability_ids: list[str] = Field(default_factory=list)
    suggested_question: str | None = Field(default=None, max_length=500)
    recommended_action: str | None = Field(default=None, max_length=120)
    humour: HumourDirective = Field(default_factory=HumourDirective)
    maximum_sentences: int = Field(default=4, ge=1, le=4)
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def exact_operational_requires_verified_fact(self) -> "SalesReplyPlan":
        if self.reply_mode == "exact_operational" and not self.verified_facts:
            raise ValueError(
                "An exact operational reply must include at least one verified fact."
            )
        if self.suggested_question and self.reply_mode == "exact_operational":
            raise ValueError(
                "Exact operational replies cannot add a sales discovery question."
            )
        return self


class SalesSessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explicit_facts: dict[str, str] = Field(default_factory=dict)
    pain_points: list[str] = Field(default_factory=list)
    interested_capabilities: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)

    @field_validator("explicit_facts")
    @classmethod
    def validate_explicit_facts(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = set(value) - ALLOWED_PROFILE_FIELDS
        if unknown:
            raise ValueError(f"Unsupported explicit profile fields: {sorted(unknown)}")
        forbidden = set(value) & FORBIDDEN_INFERENCE_FIELDS
        if forbidden:
            raise ValueError(f"Forbidden inferred fields: {sorted(forbidden)}")
        result: dict[str, str] = {}
        for key, item in value.items():
            clean = " ".join(str(item).strip().split())[:300]
            if clean:
                result[key] = clean
        return result


class SalesSessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sales-session-v1"] = "sales-session-v1"
    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    language: Language = "zh"
    stage: SalesStage = "attract"
    turn_count: int = Field(default=0, ge=0)
    effective_user_turn_count: int = Field(default=0, ge=0)
    explicit_facts: dict[str, str] = Field(default_factory=dict)
    pain_points: list[str] = Field(default_factory=list)
    interested_capabilities: list[str] = Field(default_factory=list)
    demonstrated_capabilities: list[str] = Field(default_factory=list)
    asked_fields: list[str] = Field(default_factory=list)
    declined_fields: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    booking_offer_count: int = Field(default=0, ge=0, le=2)
    contact_offer_count: int = Field(default=0, ge=0, le=1)
    booking_rejected: bool = False
    contact_rejected: bool = False
    humour_used_count: int = Field(default=0, ge=0)
    humour_themes_used: list[str] = Field(default_factory=list)
    last_humour_theme: str | None = None
    last_humour_text_hash: str | None = None
    turns_since_humour: int = Field(default=999, ge=0)
    proactive_nudge_count: int = Field(default=0, ge=0, le=2)
    last_recommended_capability: str | None = None
    last_sales_action: str | None = None
    last_booking_offer_turn: int | None = Field(default=None, ge=0)
    last_profile_question_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class SalesTelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sales-telemetry-v1"] = "sales-telemetry-v1"
    event_id: str = Field(..., min_length=1, max_length=160)
    event_type: Literal[
        "speech_started",
        "transcript_ready",
        "sales_context_ready",
        "reply_plan_ready",
        "tool_dispatched",
        "tool_verified",
        "reply_started",
        "reply_completed",
        "booking_offered",
        "booking_opened",
        "contact_offered",
        "profile_updated",
        "humour_used",
        "proactive_nudge",
        "visit_closed",
    ]
    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    occurred_at: str = Field(default_factory=utc_now_iso)
    stage: SalesStage | None = None
    data: dict[str, Any] = Field(default_factory=dict)
