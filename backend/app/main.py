import asyncio
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from app.event_bus import event_bus
from app.executor import run_task_plan_only, run_task_with_tools
from app.models import (
    AgentRequest,
    AgentResponse,
    ApprovalRequest,
    StepEvent,
    TaskCreateRequest,
    TaskSession,
)
from app.planner import plan_task
from app.presentation_api import router as presentation_router
from app.realtime_api import router as realtime_router
from app.reception_api import router as reception_router
from app.state_store import state_store
from app.task_graph import build_task_graph, task_graph_event_data
from app.task_logger import log_task_record
from app.tool_registry import run_tool
from app.turn_api import router as turn_router

app = FastAPI(title="Smart Office Agent Backend", version="0.6.0")

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
app.include_router(turn_router)
app.include_router(presentation_router)


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
        "version": "0.6.0",
        "phase": "m3a_fusion_phase_3_gate_2b",
        "capabilities": {
            "task_runtime": True,
            "realtime_voice_api": True,
            "realtime_presentation_function_calling": True,
            "agent_turn_api": True,
            "unified_turn_router": True,
            "reception_knowledge": True,
            "permission_gate": True,
            "presentation_controller": True,
            "presentation_state_verifier": True,
            "presentation_control_api": True,
            "presentation_execution_via_turn": True,
            "presentation_secondary_display": True,
            "compound_presentation_execution": True,
            "compound_task_cancellation": True,
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
    )
    event_bus.publish(
        task_id=task.task_id,
        event_type="task_created",
        message="Task session created and stored in memory.",
        data={
            "execute": req.execute,
            "step_count": len(task_graph.steps),
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
        data={},
    )
    return state_store.get_task(task_id) or task


@app.get("/agent/tasks/{task_id}/events")
async def stream_agent_task_events(
    task_id: str,
    demo: bool = Query(
        False,
        description="Send a short fake event sequence for Step 3 SSE testing.",
    ),
    timeout_seconds: float = Query(
        30.0,
        ge=0.1,
        le=300.0,
        description="Maximum time to wait for new events before closing the stream.",
    ),
):
    task = state_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    async def event_generator():
        sent_count = 0
        terminal_events = {"completed", "cancelled", "error"}

        while True:
            current = state_store.get_task(task_id)
            if current is None:
                return

            events = current.events[sent_count:]
            for event in events:
                sent_count += 1
                yield _sse_payload(event)
                if event.type in terminal_events:
                    return

            if demo and sent_count == 0:
                await asyncio.sleep(0.2)
                yield {
                    "event": "planning",
                    "data": json.dumps(
                        {
                            "task_id": task_id,
                            "message": "Demo planning event.",
                        },
                        ensure_ascii=False,
                    ),
                }
                await asyncio.sleep(0.2)
                yield {
                    "event": "completed",
                    "data": json.dumps(
                        {
                            "task_id": task_id,
                            "message": "Demo task event stream completed.",
                        },
                        ensure_ascii=False,
                    ),
                }
                return

            if current.status in {"completed", "failed", "cancelled"}:
                return

            await asyncio.sleep(0.25)

    return EventSourceResponse(event_generator(), ping=10)
