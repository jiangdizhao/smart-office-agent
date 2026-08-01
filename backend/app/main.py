import asyncio
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from app.contact_record_api import router as contact_record_router
from app.display_role_api import router as display_role_router, start_display_role_service
from app.enhanced_turn_api import router as enhanced_turn_router
from app.event_bus import event_bus
from app.executor import run_task_plan_only, run_task_with_tools
from app.general_chat_api import router as general_chat_router
from app.human_recording_api import router as human_recording_router
from app.models import (
    AgentRequest,
    AgentResponse,
    ApprovalRequest,
    StepEvent,
    TaskCreateRequest,
    TaskSession,
)
from app.office_api import router as office_router
from app.planner import plan_task
from app.presentation_api import router as presentation_router
from app.realtime_api import router as realtime_router
from app.reception_api import router as reception_router
from app.recipient_api import router as recipient_router
from app.state_store import state_store
from app.system_status_policy import install_lightweight_system_status_policy
from app.task_graph import build_task_graph, task_graph_event_data
from app.task_logger import log_task_record
from app.tool_registry import run_tool
from app.turn_api import router as turn_router

install_lightweight_system_status_policy()
start_display_role_service()

app = FastAPI(title="Smart Office Agent Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(realtime_router)
app.include_router(reception_router)
app.include_router(enhanced_turn_router)
app.include_router(turn_router)
app.include_router(presentation_router)
app.include_router(office_router)
app.include_router(recipient_router)
app.include_router(general_chat_router)
app.include_router(human_recording_router)
app.include_router(contact_record_router)
app.include_router(display_role_router)


def _sse_payload(event: StepEvent) -> dict:
    return {
        "event": event.type,
        "id": event.event_id,
        "data": json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
    }


@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "smart-office-agent-backend",
        "version": "1.0.0",
        "phase": "preemptive_visit_orchestration",
        "capabilities": {
            "task_runtime": True,
            "visit_scoped_task_ownership": True,
            "visit_task_cancellation": True,
            "approval_timeout_watchdog": True,
            "killable_office_worker_process": True,
            "background_visitor_memory_persistence": True,
            "stale_visit_result_fencing": True,
            "realtime_voice_api": True,
            "realtime_presentation_function_calling": True,
            "unified_presentation_plan": True,
            "unified_office_plan": True,
            "agent_turn_api": True,
            "unified_turn_router": True,
            "reception_knowledge": True,
            "general_backend_chat": True,
            "general_chat_not_limited_to_company_topics": True,
            "permission_gate": True,
            "conversation_memory": True,
            "conversation_recent_message_limit": 16,
            "conversation_lifecycle_state": True,
            "idle_proximity_greeting": True,
            "proximity_greeting_requires_standby": True,
            "proximity_greeting_backend_gate": True,
            "human_conversation_recording_upload": True,
            "human_conversation_diarized_transcription": True,
            "human_conversation_docx_summary": True,
            "human_conversation_docx_auto_open": True,
            "contact_records": True,
            "contact_consent_required": True,
            "touch_interaction_windows": True,
            "display_role_routing": True,
            "presentation_controller": True,
            "presentation_state_verifier": True,
            "presentation_control_api": True,
            "presentation_execution_via_turn": True,
            "presentation_secondary_display": True,
            "compound_presentation_execution": True,
            "compound_task_cancellation": True,
            "system_volume_control": True,
            "system_brightness_control": True,
            "brightness_control_mode": "deferred_explicit_only",
            "incidental_brightness_probe": False,
            "presentation_summary_artifacts": True,
            "classic_outlook_draft_creation": True,
            "outlook_draft_approval_gate": True,
            "outlook_send_second_approval_gate": True,
            "fixed_outlook_sender_account": True,
            "fixed_email_recipient": False,
            "approved_email_recipient_allowlist": True,
            "brightness_independent_recipient_directory": True,
            "arbitrary_email_recipient": False,
            "email_send_enabled": False,
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
            "general_office_execution_via_turn": False,
        },
    }


@app.post("/agent/run", response_model=AgentResponse)
def run_agent(req: AgentRequest):
    steps = plan_task(req.text)
    results = []

    if req.execute:
        for step in steps:
            if step.tool_name is None:
                continue
            result = run_tool(step.tool_name, step.args)
            results.append(result)

    return AgentResponse(
        mode="executed" if req.execute else "plan_only",
        user_request=req.text,
        steps=steps,
        results=results,
    )


@app.post("/agent/tasks", response_model=TaskSession)
async def create_agent_task(req: TaskCreateRequest):
    planned_steps = plan_task(req.text)
    task_graph = build_task_graph(planned_steps)
    task = state_store.create_task(
        user_request=req.text,
        execute=req.execute,
        task_graph=task_graph,
        owner_conversation_id=req.conversation_id,
        owner_visit_id=req.visit_id,
        owner_actor_type=req.actor_type,
    )
    event_bus.publish(
        task_id=task.task_id,
        event_type="task_created",
        message="Task session created and stored in memory.",
        data={
            "execute": req.execute,
            "step_count": len(task_graph.steps),
            "owner_conversation_id": req.conversation_id,
            "owner_visit_id": req.visit_id,
            "owner_actor_type": req.actor_type,
            "note": "Task graph is available; executor has been scheduled.",
        },
    )
    event_bus.publish(
        task_id=task.task_id,
        event_type="planning",
        message="Planner output converted into task graph.",
        data=task_graph_event_data(task_graph),
    )

    if req.execute:
        asyncio.create_task(run_task_with_tools(task.task_id))
    else:
        asyncio.create_task(run_task_plan_only(task.task_id))

    return task


@app.get("/agent/tasks/{task_id}", response_model=TaskSession)
def get_agent_task(task_id: str):
    task = state_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


@app.post("/agent/tasks/{task_id}/approval", response_model=TaskSession)
def handle_agent_task_approval(task_id: str, req: ApprovalRequest):
    task = state_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    waiting_step = next(
        (step for step in task.steps if step.status == "waiting_approval"),
        None,
    )
    if waiting_step is None:
        raise HTTPException(
            status_code=409,
            detail=f"Task is not waiting for approval: {task_id}",
        )

    state_store.set_approval(task_id, waiting_step.step_id, req)
    log_task_record(
        task_id,
        "approval",
        {
            "step_id": waiting_step.step_id,
            "step_index": waiting_step.index,
            "action": req.action,
            "note": req.note,
            "owner_visit_id": task.owner_visit_id,
        },
    )
    return state_store.get_task(task_id) or task


@app.post("/agent/tasks/{task_id}/cancel", response_model=TaskSession)
def cancel_agent_task(task_id: str):
    task = state_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if task.status in {"completed", "failed", "cancelled"}:
        return task

    state_store.update_pending_steps(
        task_id,
        "cancelled",
        message="Task cancellation requested.",
    )
    state_store.set_status(
        task_id,
        "cancelled",
        summary="Task cancellation requested.",
    )
    event_bus.publish(
        task_id=task_id,
        event_type="cancelled",
        message="Task cancellation requested.",
        data={"owner_visit_id": task.owner_visit_id},
    )
    return state_store.get_task(task_id) or task


@app.get("/agent/tasks/{task_id}/events")
async def stream_agent_task_events(
    task_id: str,
    after: str | None = Query(default=None),
):
    if state_store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    async def event_generator():
        async for event in event_bus.subscribe(task_id, after_event_id=after):
            yield _sse_payload(event)

    return EventSourceResponse(event_generator())
