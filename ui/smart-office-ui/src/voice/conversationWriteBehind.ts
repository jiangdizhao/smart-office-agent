const WRITE_BEHIND_PATH = /^\/api\/conversations\/([^/]+)\/(turn-start|turn-complete|task-state)$/

type RuntimeWindow = Window & {
  __SMART_OFFICE_CONVERSATION_WRITE_BEHIND_INSTALLED__?: boolean
}

const queues = new Map<string, Promise<void>>()

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  return String(init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
}

function requestUrl(input: RequestInfo | URL): URL | null {
  try {
    return new URL(input instanceof Request ? input.url : String(input), window.location.href)
  } catch {
    return null
  }
}

function cloneInit(input: RequestInfo | URL, init?: RequestInit): RequestInit | undefined {
  if (input instanceof Request) {
    return {
      method: init?.method ?? input.method,
      headers: init?.headers ?? input.headers,
      body: init?.body,
      credentials: init?.credentials ?? input.credentials,
      cache: init?.cache ?? input.cache,
      redirect: init?.redirect ?? input.redirect,
      referrer: init?.referrer ?? input.referrer,
      referrerPolicy: init?.referrerPolicy ?? input.referrerPolicy,
      integrity: init?.integrity ?? input.integrity,
      keepalive: init?.keepalive ?? input.keepalive,
      mode: init?.mode ?? input.mode,
      signal: init?.signal ?? input.signal,
    }
  }
  return init ? { ...init } : undefined
}

function queueWrite(
  key: string,
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  nativeFetch: typeof window.fetch,
): void {
  const previous = queues.get(key) ?? Promise.resolve()
  const operation = previous
    .catch(() => undefined)
    .then(async () => {
      const startedAt = performance.now()
      try {
        const response = await nativeFetch(input, cloneInit(input, init))
        if (!response.ok) {
          console.warn('[ConversationWriteBehind] write-rejected', {
            key,
            status: response.status,
            detail: await response.text().catch(() => ''),
          })
          return
        }
        console.info('[ConversationWriteBehind] write-complete', {
          key,
          elapsedMs: Math.round(performance.now() - startedAt),
        })
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') return
        console.warn('[ConversationWriteBehind] write-failed', {
          key,
          message: error instanceof Error ? error.message : String(error),
        })
      }
    })
    .finally(() => {
      if (queues.get(key) === operation) queues.delete(key)
    })
  queues.set(key, operation)
}

export function installConversationWriteBehind(): void {
  const runtimeWindow = window as RuntimeWindow
  if (runtimeWindow.__SMART_OFFICE_CONVERSATION_WRITE_BEHIND_INSTALLED__) return
  runtimeWindow.__SMART_OFFICE_CONVERSATION_WRITE_BEHIND_INSTALLED__ = true
  const nativeFetch = window.fetch.bind(window)

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = requestUrl(input)
    const match = url?.pathname.match(WRITE_BEHIND_PATH)
    if (requestMethod(input, init) !== 'POST' || !match) {
      return await nativeFetch(input, init)
    }

    const conversationId = decodeURIComponent(match[1])
    const operation = match[2]
    queueWrite(conversationId, input, init, nativeFetch)
    window.dispatchEvent(new CustomEvent('smartoffice:conversation-write-behind-queued', {
      detail: { conversationId, operation },
    }))
    return new Response(JSON.stringify({
      ok: true,
      accepted: true,
      write_behind: true,
      operation,
    }), {
      status: 202,
      headers: {
        'Content-Type': 'application/json; charset=utf-8',
        'X-Smart-Office-Write-Behind': 'true',
      },
    })
  }
}
