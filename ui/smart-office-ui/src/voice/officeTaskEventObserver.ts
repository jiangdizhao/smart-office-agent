const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const CACHE_FRESH_MS = 2_000
const REFRESH_COALESCE_MS = 80

type TaskSnapshot = {
  task_id?: string
  status?: string
  [key: string]: unknown
}

type CachedTask = {
  snapshot: TaskSnapshot
  updatedAt: number
}

type TaskRuntimeWindow = Window & {
  __SMART_OFFICE_TASK_EVENT_OBSERVER_INSTALLED__?: boolean
  __SMART_OFFICE_TASK_CACHE__?: Record<string, CachedTask>
}

const cache = new Map<string, CachedTask>()
const streams = new Map<string, EventSource>()
const refreshTimers = new Map<string, number>()

function taskIdFromUrl(input: RequestInfo | URL): string | null {
  const source = input instanceof Request ? input.url : String(input)
  try {
    const url = new URL(source, window.location.href)
    const match = url.pathname.match(/^\/agent\/tasks\/([^/]+)$/)
    return match ? decodeURIComponent(match[1]) : null
  } catch {
    return null
  }
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  return String(init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
}

function publishCache(): void {
  const record = Object.fromEntries(cache.entries())
  ;(window as TaskRuntimeWindow).__SMART_OFFICE_TASK_CACHE__ = record
}

function cacheSnapshot(taskId: string, snapshot: TaskSnapshot): void {
  cache.set(taskId, { snapshot, updatedAt: performance.now() })
  publishCache()
  window.dispatchEvent(new CustomEvent('smartoffice:office-task-snapshot', {
    detail: { taskId, snapshot },
  }))
}

async function refreshTask(taskId: string, nativeFetch: typeof window.fetch): Promise<void> {
  try {
    const response = await nativeFetch(
      `${API_BASE_URL}/agent/tasks/${encodeURIComponent(taskId)}`,
      { headers: { Accept: 'application/json' } },
    )
    if (!response.ok) return
    const snapshot = await response.json() as TaskSnapshot
    cacheSnapshot(taskId, snapshot)
    if (['completed', 'failed', 'cancelled'].includes(String(snapshot.status ?? ''))) {
      streams.get(taskId)?.close()
      streams.delete(taskId)
    }
  } catch (error) {
    console.warn('[OfficeTaskEvents] snapshot-refresh-failed', {
      taskId,
      message: error instanceof Error ? error.message : String(error),
    })
  }
}

function scheduleRefresh(taskId: string, nativeFetch: typeof window.fetch): void {
  if (refreshTimers.has(taskId)) return
  const timer = window.setTimeout(() => {
    refreshTimers.delete(taskId)
    void refreshTask(taskId, nativeFetch)
  }, REFRESH_COALESCE_MS)
  refreshTimers.set(taskId, timer)
}

function subscribe(taskId: string, nativeFetch: typeof window.fetch): void {
  if (!taskId || streams.has(taskId)) return
  const source = new EventSource(`${API_BASE_URL}/agent/tasks/${encodeURIComponent(taskId)}/events`)
  streams.set(taskId, source)
  const eventTypes = [
    'task_created',
    'planning',
    'step_started',
    'approval_required',
    'approval_resolved',
    'tool_result',
    'verification_result',
    'completed',
    'cancelled',
    'error',
  ]
  for (const eventType of eventTypes) {
    source.addEventListener(eventType, (event) => {
      window.dispatchEvent(new CustomEvent('smartoffice:office-task-event', {
        detail: {
          taskId,
          eventType,
          data: (event as MessageEvent<string>).data,
          eventId: (event as MessageEvent<string>).lastEventId,
        },
      }))
      scheduleRefresh(taskId, nativeFetch)
      if (['completed', 'cancelled', 'error'].includes(eventType)) {
        window.setTimeout(() => {
          source.close()
          streams.delete(taskId)
        }, 500)
      }
    })
  }
  source.addEventListener('error', () => {
    console.info('[OfficeTaskEvents] stream-reconnecting', {
      taskId,
      readyState: source.readyState,
    })
  })
  void refreshTask(taskId, nativeFetch)
}

function cachedResponse(taskId: string): Response | null {
  const item = cache.get(taskId)
  if (!item || performance.now() - item.updatedAt > CACHE_FRESH_MS) return null
  return new Response(JSON.stringify(item.snapshot), {
    status: 200,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'X-Smart-Office-Task-Cache': 'sse',
    },
  })
}

async function observeTaskCreationResponse(
  response: Response,
  nativeFetch: typeof window.fetch,
): Promise<void> {
  if (!response.ok) return
  try {
    const payload = await response.clone().json() as TaskSnapshot & { task_id?: string }
    const taskId = String(payload.task_id ?? '').trim()
    if (!taskId) return
    if (payload.status || payload.steps) cacheSnapshot(taskId, payload)
    subscribe(taskId, nativeFetch)
  } catch {
    // Non-JSON or unrelated responses are ignored.
  }
}

export function installOfficeTaskEventObserver(): void {
  const runtimeWindow = window as TaskRuntimeWindow
  if (runtimeWindow.__SMART_OFFICE_TASK_EVENT_OBSERVER_INSTALLED__) return
  runtimeWindow.__SMART_OFFICE_TASK_EVENT_OBSERVER_INSTALLED__ = true
  const nativeFetch = window.fetch.bind(window)

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const method = requestMethod(input, init)
    const taskId = taskIdFromUrl(input)
    if (method === 'GET' && taskId) {
      const cached = cachedResponse(taskId)
      if (cached) return cached
    }

    const response = await nativeFetch(input, init)
    const source = input instanceof Request ? input.url : String(input)
    let pathname = ''
    try {
      pathname = new URL(source, window.location.href).pathname
    } catch {
      pathname = ''
    }
    if (
      method === 'POST'
      && (pathname === '/agent/office-turn' || pathname === '/agent/tasks')
    ) {
      void observeTaskCreationResponse(response, nativeFetch)
    } else if (method === 'GET' && taskId && response.ok) {
      void response.clone().json().then((snapshot: TaskSnapshot) => {
        cacheSnapshot(taskId, snapshot)
        subscribe(taskId, nativeFetch)
      }).catch(() => undefined)
    }
    return response
  }
}
