from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    text: str = Field(..., description="User request")
    execute: bool = Field(False, description="Whether to execute tools or only plan")


class PlannedStep(BaseModel):
    index: int
    title: str
    tool_name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False


class ToolResult(BaseModel):
    tool_name: str
    ok: bool
    message: str
    launched_pid: int | None = None
    expected_process_names: list[str] = Field(default_factory=list)
    expected_window_keywords: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    ok: bool
    message: str
    process_ok: bool | None = None
    window_ok: bool | None = None
    expected_process_names: list[str] = Field(default_factory=list)
    found_process_names: list[str] = Field(default_factory=list)
    expected_window_keywords: list[str] = Field(default_factory=list)
    found_window_titles: list[str] = Field(default_factory=list)
    require_window_match: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime


class AgentResponse(BaseModel):
    mode: Literal["plan_only", "executed"]
    user_request: str
    steps: list[PlannedStep]
    results: list[ToolResult] = Field(default_factory=list)


TaskStatus = Literal[
    "created",
    "planning",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
]

StepStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "verifying",
    "succeeded",
    "failed",
    "skipped",
    "cancelled",
]

EventType = Literal[
    "task_created",
    "planning",
    "step_started",
    "tool_result",
    "verification_result",
    "approval_required",
    "approval_resolved",
    "completed",
    "cancelled",
    "error",
]

ApprovalAction = Literal["approve", "cancel", "skip", "takeover"]


class TaskCreateRequest(BaseModel):
    text: str = Field(..., description="User request")
    execute: bool = Field(False, description="Whether this task should execute tools")


class ApprovalRequest(BaseModel):
    action: ApprovalAction
    note: str | None = None


class TaskStep(BaseModel):
    step_id: str
    index: int
    title: str
    tool_name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
    status: StepStatus = "pending"
    message: str | None = None
    result: ToolResult | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskGraph(BaseModel):
    source: str = "rule_planner"
    steps: list[TaskStep] = Field(default_factory=list)
    created_at: datetime


class StepEvent(BaseModel):
    event_id: str
    task_id: str
    step_id: str | None = None
    type: EventType
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class TaskSession(BaseModel):
    task_id: str
    user_request: str
    execute: bool
    status: TaskStatus = "created"
    steps: list[TaskStep] = Field(default_factory=list)
    events: list[StepEvent] = Field(default_factory=list)
    summary: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
