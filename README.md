# Smart Office Agent

Smart Office Agent is a local Windows automation prototype for planning and executing office workflows. It exposes a FastAPI backend, a React dashboard, a task graph runtime, server-sent events, approval gates, local logging, and a Windows controller for opening desktop applications.

The current implementation is Milestone 2 focused: the agent can create task sessions, convert planner output into a task graph, stream live execution events to the browser, pause for human approval, run in simulation mode, and optionally call local Windows tools.

## Features

- FastAPI backend with task creation and task status APIs.
- Server-sent events endpoint for real-time task updates.
- In-memory task state store and event bus.
- Planner output converted into a task graph.
- Executor with two modes:
  - `execute=false`: simulated plan-only flow.
  - `execute=true`: runs registered local tools.
- Windows controller tools for Edge, Zoom, Word, Excel, PowerPoint, OneNote, and a sample document.
- Structured `ToolResult` responses with timeouts.
- Verifier that checks process and window state.
- Human confirmation gate for sensitive or non-tool steps.
- Local JSONL task logs.
- React UI using `POST /agent/tasks` and `EventSource`.
- Regression script for the Milestone 2 backend flow.

## Repository Layout

```text
backend/
  app/
    main.py                  FastAPI application and API routes
    planner.py               Rule-based planner prototype
    task_graph.py            Planner-to-task-graph conversion
    executor.py              Plan-only and tool execution flows
    state_store.py           In-memory task state
    event_bus.py             In-memory task event stream
    task_logger.py           Local JSONL task logging
    verifier.py              Process/window verification
    tool_registry.py         Tool dispatch with timeout handling
    tools/windows_controller.py
                             Windows desktop automation helpers
  scripts/
    regression_milestone2.py Backend regression smoke test
  requirements-smartoffice.txt

ui/smart-office-ui/
  src/App.tsx                React dashboard
  src/App.css                Dashboard styling

.gitignore                  Excludes private docs, data, logs, secrets, caches
```

## Requirements

- Windows 10 or Windows 11.
- Conda environment named `smartoffice`.
- Python 3.11.15 in that environment.
- Node.js and npm for the React UI.
- Microsoft Office applications and Zoom are optional, but required for real Windows tool execution.

The real tool execution mode depends on local desktop automation packages such as `pywin32`, `pywinauto`, and related Windows APIs.

## Setup

Activate the conda environment:

```powershell
conda activate smartoffice
```

Install backend dependencies:

```powershell
pip install -r backend/requirements-smartoffice.txt
```

Install frontend dependencies:

```powershell
cd ui/smart-office-ui
npm install
```

## Run The Backend

From the repository root:

```powershell
conda activate smartoffice
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/
```

## Run The Frontend

From `ui/smart-office-ui`:

```powershell
npm run dev
```

Open:

```text
http://127.0.0.1:5173/
```

## API Examples

Create a simulated task:

```powershell
$body = @{ text = "meeting prepare"; execute = $false } | ConvertTo-Json
$task = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/agent/tasks -ContentType "application/json" -Body $body
$task.task_id
```

Get task status:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/agent/tasks/$($task.task_id)
```

Approve, skip, cancel, or take over a waiting approval step:

```powershell
$approval = @{ action = "skip"; note = "manual test" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/agent/tasks/$($task.task_id)/approval -ContentType "application/json" -Body $approval
```

Stream events with curl:

```powershell
curl.exe -N http://127.0.0.1:8000/agent/tasks/$($task.task_id)/events
```

Run real local tools:

```powershell
$body = @{ text = "meeting prepare"; execute = $true } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/agent/tasks -ContentType "application/json" -Body $body
```

Use `execute=true` only on a trusted local machine, because it can open desktop applications.

## Regression Test

Start the backend first, then run:

```powershell
conda activate smartoffice
python backend/scripts/regression_milestone2.py
```

Expected flow:

- backend health check passes
- task is created
- approval gate is reached
- approval is skipped
- task completes
- local JSONL log is created

## Git Safety Notes

This repository intentionally ignores private docs, local data, logs, secrets, Python caches, and frontend build artifacts.

Before pushing, verify that sensitive files are not tracked:

```powershell
git ls-files | Select-String '^(data/|doc/|docs/|logs/)|critical\.txt|__pycache__|\.pyc$|\.vscode/'
```

The command should produce no output.

## Current Status

Milestone 2 is implemented through:

- backend task/session model
- task graph and event bus
- SSE endpoint
- planner-to-task-graph integration
- simulated executor
- Windows controller execution with tool timeouts
- verifier
- React dashboard integration
- human approval gate
- local logs and regression script

Future work can replace the rule-based planner with a stronger LLM planner, persist task state beyond memory, and add broader verification for document and application state.
