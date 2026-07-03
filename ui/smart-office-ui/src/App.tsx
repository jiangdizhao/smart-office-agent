import { useRef, useState } from "react";
import "./App.css";

type TaskStatus =
  | "created"
  | "planning"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

type StepStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "verifying"
  | "succeeded"
  | "failed"
  | "skipped"
  | "cancelled";

type ToolResult = {
  tool_name: string;
  ok: boolean;
  message: string;
  launched_pid: number | null;
  expected_process_names: string[];
  expected_window_keywords: string[];
  artifacts: string[];
  raw: Record<string, unknown>;
  data: Record<string, unknown>;
};

type TaskStep = {
  step_id: string;
  index: number;
  title: string;
  tool_name: string | null;
  args: Record<string, unknown>;
  requires_confirmation: boolean;
  status: StepStatus;
  message: string | null;
  result: ToolResult | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

type StepEvent = {
  event_id: string;
  task_id: string;
  step_id: string | null;
  type:
    | "task_created"
    | "planning"
    | "step_started"
    | "tool_result"
    | "verification_result"
    | "approval_required"
    | "approval_resolved"
    | "completed"
    | "cancelled"
    | "error";
  message: string;
  data: Record<string, unknown>;
  timestamp: string;
};

type TaskSession = {
  task_id: string;
  user_request: string;
  execute: boolean;
  status: TaskStatus;
  steps: TaskStep[];
  events: StepEvent[];
  summary: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

const API_BASE = "http://localhost:8000";
const EVENT_TYPES: StepEvent["type"][] = [
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
];

function statusLabel(status: TaskStatus | StepStatus) {
  const labels: Record<string, string> = {
    created: "已创建",
    planning: "规划中",
    running: "运行中",
    waiting_approval: "待确认",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    pending: "等待中",
    verifying: "验证中",
    succeeded: "成功",
    skipped: "已跳过",
  };
  return labels[status] ?? status;
}

function statusClass(status: TaskStatus | StepStatus) {
  if (status === "completed" || status === "succeeded") return "good";
  if (status === "failed" || status === "cancelled") return "bad";
  if (status === "running" || status === "planning" || status === "verifying") {
    return "active";
  }
  if (status === "waiting_approval") return "warn";
  return "idle";
}

function eventTime(timestamp: string) {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function applyEventToTask(task: TaskSession, event: StepEvent): TaskSession {
  const hasEvent = task.events.some((item) => item.event_id === event.event_id);
  const events = hasEvent ? task.events : [...task.events, event];
  const steps = task.steps.map((step) => {
    if (step.step_id !== event.step_id) return step;

    if (event.type === "step_started") {
      return {
        ...step,
        status: "running" as StepStatus,
        message: event.message,
        started_at: step.started_at ?? event.timestamp,
      };
    }

    if (event.type === "tool_result") {
      const result = event.data as unknown as ToolResult;
      return {
        ...step,
        status: result.ok ? ("verifying" as StepStatus) : ("failed" as StepStatus),
        message: event.message,
        result,
      };
    }

    if (event.type === "verification_result") {
      const ok = Boolean(event.data.ok);
      return {
        ...step,
        status: ok ? ("succeeded" as StepStatus) : ("failed" as StepStatus),
        message: event.message,
        finished_at: event.timestamp,
      };
    }

    if (event.type === "approval_required") {
      return {
        ...step,
        status: "waiting_approval" as StepStatus,
        message: event.message,
      };
    }

    if (event.type === "approval_resolved") {
      const action = event.data.action;
      if (action === "skip") {
        return {
          ...step,
          status: "skipped" as StepStatus,
          message: event.message,
          finished_at: event.timestamp,
        };
      }
      if (action === "cancel" || action === "takeover") {
        return {
          ...step,
          status: "cancelled" as StepStatus,
          message: event.message,
          finished_at: event.timestamp,
        };
      }
      return {
        ...step,
        status: "running" as StepStatus,
        message: event.message,
      };
    }

    if (event.type === "cancelled") {
      return {
        ...step,
        status: "cancelled" as StepStatus,
        message: event.message,
        finished_at: event.timestamp,
      };
    }

    if (event.type === "error") {
      return {
        ...step,
        status: "failed" as StepStatus,
        message: event.message,
        finished_at: event.timestamp,
      };
    }

    return step;
  });

  let status = task.status;
  if (event.type === "planning") status = "planning";
  if (event.type === "step_started" || event.type === "tool_result") status = "running";
  if (event.type === "approval_required") status = "waiting_approval";
  if (event.type === "completed") status = "completed";
  if (event.type === "cancelled") status = "cancelled";
  if (event.type === "error") status = "failed";

  return {
    ...task,
    status,
    steps,
    events,
    summary:
      event.type === "completed" || event.type === "cancelled" || event.type === "error"
        ? event.message
        : task.summary,
    updated_at: event.timestamp,
    completed_at:
      event.type === "completed" || event.type === "cancelled" || event.type === "error"
        ? event.timestamp
        : task.completed_at,
  };
}

function App() {
  const [text, setText] = useState("帮我准备下午2点的项目会议");
  const [task, setTask] = useState<TaskSession | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  function closeEventStream() {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    setStreaming(false);
  }

  async function fetchTask(taskId: string) {
    const res = await fetch(`${API_BASE}/agent/tasks/${taskId}`);
    if (!res.ok) throw new Error(`Task fetch failed: ${res.status}`);
    const data = (await res.json()) as TaskSession;
    setTask(data);
  }

  function connectEvents(taskId: string) {
    closeEventStream();
    setStreaming(true);

    const source = new EventSource(
      `${API_BASE}/agent/tasks/${taskId}/events?timeout_seconds=120`,
    );
    eventSourceRef.current = source;

    EVENT_TYPES.forEach((eventType) => {
      source.addEventListener(eventType, (message) => {
        const event = JSON.parse((message as MessageEvent).data) as StepEvent;
        setTask((current) => (current ? applyEventToTask(current, event) : current));

        if (event.type === "completed" || event.type === "cancelled" || event.type === "error") {
          source.close();
          eventSourceRef.current = null;
          setStreaming(false);
          void fetchTask(taskId).catch((err) => setError(String(err)));
        }
      });
    });

    source.onerror = () => {
      setError("事件流连接已关闭或中断。");
      closeEventStream();
    };
  }

  async function createTask(execute: boolean) {
    closeEventStream();
    setTask(null);
    setError(null);
    setStreaming(true);

    try {
      const res = await fetch(`${API_BASE}/agent/tasks`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ text, execute }),
      });

      if (!res.ok) throw new Error(`Backend error: ${res.status}`);

      const data = (await res.json()) as TaskSession;
      setTask(data);
      connectEvents(data.task_id);
    } catch (err) {
      setError(String(err));
      closeEventStream();
    }
  }

  async function sendApproval(action: "approve" | "cancel" | "skip" | "takeover") {
    if (!task) return;

    setError(null);
    try {
      const res = await fetch(`${API_BASE}/agent/tasks/${task.task_id}/approval`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ action }),
      });

      if (!res.ok) throw new Error(`Approval failed: ${res.status}`);
      const data = (await res.json()) as TaskSession;
      setTask(data);
    } catch (err) {
      setError(String(err));
    }
  }

  const activeStepCount = task?.steps.filter((step) => step.status !== "pending").length ?? 0;
  const approvalStep = task?.steps.find((step) => step.status === "waiting_approval") ?? null;

  return (
    <main className="screen">
      <section className="topbar">
        <div>
          <p className="eyebrow">Smart Office Agent v0.2</p>
          <h1>统一智能办公 Agent 控制台</h1>
        </div>
        <div className="backend-pill">
          <span className={streaming ? "status-dot live" : "status-dot"} />
          Backend: localhost:8000
        </div>
      </section>

      <section className="command-panel">
        <label htmlFor="request">用户请求</label>
        <textarea
          id="request"
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={3}
        />

        <div className="actions">
          <button disabled={streaming} onClick={() => createTask(false)}>
            计划演示
          </button>
          <button disabled={streaming} onClick={() => createTask(true)} className="primary">
            执行工具
          </button>
        </div>
      </section>

      {error && <section className="notice bad">错误：{error}</section>}

      {task?.status === "waiting_approval" && approvalStep && (
        <section className="approval-panel">
          <div>
            <span className="label">Approval</span>
            <strong>{approvalStep.title}</strong>
            <p>{approvalStep.message ?? "等待人工确认。"}</p>
          </div>
          <div className="approval-actions">
            <button className="primary" onClick={() => sendApproval("approve")}>
              确认
            </button>
            <button onClick={() => sendApproval("skip")}>跳过</button>
            <button onClick={() => sendApproval("takeover")}>人工接管</button>
            <button className="danger" onClick={() => sendApproval("cancel")}>
              取消
            </button>
          </div>
        </section>
      )}

      {task && (
        <>
          <section className="task-strip">
            <div>
              <span className="label">Task</span>
              <strong>{task.task_id}</strong>
            </div>
            <div>
              <span className="label">Status</span>
              <span className={`badge ${statusClass(task.status)}`}>
                {statusLabel(task.status)}
              </span>
            </div>
            <div>
              <span className="label">Mode</span>
              <strong>{task.execute ? "Windows Controller" : "Plan-only"}</strong>
            </div>
            <div>
              <span className="label">Steps</span>
              <strong>
                {activeStepCount}/{task.steps.length}
              </strong>
            </div>
          </section>

          <section className="workspace">
            <div className="panel timeline-panel">
              <div className="panel-title">
                <h2>步骤</h2>
                <span className="muted">{task.summary}</span>
              </div>
              <ol className="step-list">
                {task.steps.map((step) => (
                  <li key={step.step_id} className="step-row">
                    <span className={`step-index ${statusClass(step.status)}`}>
                      {step.index}
                    </span>
                    <div className="step-body">
                      <div className="step-head">
                        <strong>{step.title}</strong>
                        <span className={`badge ${statusClass(step.status)}`}>
                          {statusLabel(step.status)}
                        </span>
                      </div>
                      <div className="meta">
                        {step.tool_name ?? "reasoning_step"}
                        {step.requires_confirmation ? " · requires confirmation" : ""}
                      </div>
                      {step.message && <p className="step-message">{step.message}</p>}
                      {step.result && (
                        <div className="result-line">
                          <span className={step.result.ok ? "ok" : "bad-text"}>
                            {step.result.ok ? "Tool OK" : "Tool Failed"}
                          </span>
                          {step.result.launched_pid ? (
                            <span>PID {step.result.launched_pid}</span>
                          ) : null}
                          {step.result.expected_process_names.length > 0 ? (
                            <span>{step.result.expected_process_names.join(", ")}</span>
                          ) : null}
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            </div>

            <div className="panel event-panel">
              <div className="panel-title">
                <h2>事件</h2>
                <span className="muted">{task.events.length} events</span>
              </div>
              <div className="event-list">
                {task.events.map((event) => (
                  <div key={event.event_id} className="event-row">
                    <time>{eventTime(event.timestamp)}</time>
                    <span className={`event-type ${event.type}`}>{event.type}</span>
                    <p>{event.message}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>
        </>
      )}
    </main>
  );
}

export default App;
