from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Language = Literal["zh", "en"]
SemanticDomain = Literal[
    "office",
    "interaction",
    "sales",
    "general",
    "identity",
    "privacy",
    "unknown",
]
ActionMode = Literal[
    "execute",
    "answer_only",
    "clarify",
    "request_confirmation",
    "reject",
    "delegate",
]
RiskLevel = Literal["none", "low", "business_state", "external_effect"]
ActionPolarity = Literal["affirmed", "negated", "uncertain"]
SpeechAct = Literal[
    "command",
    "question",
    "explanation_request",
    "hypothetical",
    "quotation",
    "conditional",
    "response",
    "statement",
]


class SemanticEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(..., min_length=1, max_length=500)
    evidence: str = Field(..., min_length=1, max_length=500)

    @field_validator("value", "evidence")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.strip().split())


class SemanticAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verb: Literal[
        "open",
        "close",
        "start",
        "stop",
        "set",
        "next",
        "previous",
        "goto",
        "explain",
        "introduce",
        "book",
        "register",
        "record",
        "summarise",
        "show",
        "send",
        "create",
        "unknown",
    ]
    target: str = Field(..., min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    polarity: ActionPolarity = "affirmed"
    speech_act: SpeechAct = "command"
    evidence: str = Field(default="", max_length=500)
    sequence: int = Field(default=1, ge=1, le=20)

    @field_validator("target", "evidence")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.strip().split())


class SemanticProfileExtraction(BaseModel):
    """Only facts explicitly present in the current utterance are allowed."""

    model_config = ConfigDict(extra="forbid")

    explicit_facts: dict[str, SemanticEvidence] = Field(default_factory=dict)
    pain_points: list[SemanticEvidence] = Field(default_factory=list)
    interested_capabilities: list[SemanticEvidence] = Field(default_factory=list)
    objections: list[SemanticEvidence] = Field(default_factory=list)
    declined_fields: list[str] = Field(default_factory=list)
    demo_capability_id: str | None = Field(default=None, max_length=120)
    explicit_demo_request: bool = False
    booking_intent: Literal["none", "accept", "reject", "direct"] = "none"
    contact_intent: Literal["none", "accept", "reject", "direct"] = "none"
    cost_question: bool = False
    privacy_question: bool = False
    ordinary_chatbot_objection: bool = False
    disengaged: bool = False
    brief_affirmation: bool = False
    brief_rejection: bool = False
    direct_operational_command: bool = False
    sales_relevant: bool = False


class SemanticRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["semantic-route-v1"] = "semantic-route-v1"
    primary_intent: Literal[
        "self_introduction",
        "capability_explanation",
        "general_question",
        "conversation_continue",
        "conversation_end",
        "office_action",
        "presentation_action",
        "email_action",
        "system_action",
        "application_action",
        "multi_action_workflow",
        "open_contact_registration",
        "open_meeting_booking",
        "open_recording",
        "open_transcript",
        "open_result_center",
        "close_interaction_panel",
        "provide_profile_context",
        "express_pain_point",
        "request_recommendation",
        "request_demo",
        "booking_response",
        "contact_response",
        "sales_objection",
        "cost_question",
        "privacy_question",
        "unknown",
    ]
    domain: SemanticDomain
    action_mode: ActionMode
    confidence: float = Field(..., ge=0.0, le=1.0)
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=800)
    actions: list[SemanticAction] = Field(default_factory=list, max_length=12)
    negated_actions: list[SemanticAction] = Field(default_factory=list, max_length=12)
    entities: dict[str, Any] = Field(default_factory=dict)
    sales_signals: list[str] = Field(default_factory=list, max_length=20)
    conversation_reference: str | None = Field(default=None, max_length=500)
    risk: RiskLevel = "none"
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    profile_extraction: SemanticProfileExtraction = Field(
        default_factory=SemanticProfileExtraction
    )
    complexity: Literal["simple", "complex", "not_applicable"] = "not_applicable"
    answer_engine: Literal["realtime", "terra", "office_interpreter", "backend"] = "backend"
    source: Literal["fast_path", "semantic_model", "pending_intent", "safe_fallback"]

    @model_validator(mode="after")
    def validate_decision(self) -> "SemanticRoute":
        if self.requires_clarification and not self.clarification_question:
            raise ValueError("A clarification question is required when clarification is requested.")
        if self.action_mode == "execute" and not self.actions:
            raise ValueError("Execute decisions require at least one action.")
        if any(action.polarity != "affirmed" for action in self.actions):
            raise ValueError("Executable actions must be affirmed; negated actions belong in negated_actions.")
        return self


class RuntimeRouteContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interaction_panel: str | None = Field(default=None, max_length=120)
    active_tool: str | None = Field(default=None, max_length=160)
    assistant_speaking: bool = False
    visitor_present: bool = True


class RecentTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system"]
    text: str = Field(..., min_length=1, max_length=4000)


class SemanticRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str | None = Field(default=None, max_length=160)
    language: Language = "zh"
    actor_type: Literal["visitor", "employee", "operator"] = "visitor"
    text: str = Field(..., min_length=1, max_length=12000)
    recent_turns: list[RecentTurn] = Field(default_factory=list, max_length=12)
    runtime_context: RuntimeRouteContext = Field(default_factory=RuntimeRouteContext)


class PendingIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    visit_id: str
    intent_type: Literal[
        "booking_offer",
        "contact_offer",
        "demo_choice",
        "confirmation",
        "clarification",
    ]
    source_turn_id: str | None = None
    created_at_epoch: float
    remaining_user_turns: int = Field(default=1, ge=0, le=3)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PendingIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(..., min_length=1, max_length=160)
    visit_id: str = Field(..., min_length=1, max_length=160)
    intent_type: PendingIntent.model_fields["intent_type"].annotation
    source_turn_id: str | None = Field(default=None, max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    phase: Literal["unified_semantic_routing"] = "unified_semantic_routing"
    mode: Literal["legacy", "shadow", "unified"]
    route: SemanticRoute
    final_policy_decision: ActionMode
    policy_reason_codes: list[str]
    pending_intent_used: str | None = None
    legacy_comparison: dict[str, Any] | None = None
    decision_id: str
    model: str | None = None
    elapsed_ms: int = Field(ge=0)
