from app.models import PlannedStep, TaskGraph, TaskStep
from app.state_store import utc_now


def build_task_graph(planned_steps: list[PlannedStep]) -> TaskGraph:
    created_at = utc_now()
    steps = [
        TaskStep(
            step_id=f"step-{step.index}",
            index=step.index,
            title=step.title,
            tool_name=step.tool_name,
            args=step.args,
            requires_confirmation=step.requires_confirmation,
            created_at=created_at,
        )
        for step in planned_steps
    ]
    return TaskGraph(source="rule_planner", steps=steps, created_at=created_at)


def task_graph_event_data(task_graph: TaskGraph) -> dict:
    return {
        "source": task_graph.source,
        "step_count": len(task_graph.steps),
        "steps": [
            {
                "step_id": step.step_id,
                "index": step.index,
                "title": step.title,
                "tool_name": step.tool_name,
                "requires_confirmation": step.requires_confirmation,
                "status": step.status,
            }
            for step in task_graph.steps
        ],
    }
