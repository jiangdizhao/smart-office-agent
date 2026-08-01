export type SessionMessageRole = 'user' | 'assistant' | 'system'

export type SessionMessageEvent = {
  eventId: string
  conversationId: string
  visitId: string | null
  role: SessionMessageRole
  text: string
  timestamp: string
  source: string
}

const CHANNEL_NAME = 'smartoffice-current-session-events-v1'

function newEventId(): string {
  return `session-event-${Date.now()}-${crypto.randomUUID()}`
}

export function publishSessionMessage(input: {
  conversationId: string
  visitId?: string | null
  role: SessionMessageRole
  text: string
  source: string
}): SessionMessageEvent | null {
  const text = input.text.trim()
  if (!input.conversationId.trim() || !text) return null
  const event: SessionMessageEvent = {
    eventId: newEventId(),
    conversationId: input.conversationId.trim(),
    visitId: input.visitId?.trim() || null,
    role: input.role,
    text,
    timestamp: new Date().toISOString(),
    source: input.source,
  }
  try {
    const channel = new BroadcastChannel(CHANNEL_NAME)
    channel.postMessage(event)
    channel.close()
  } catch {
    localStorage.setItem(CHANNEL_NAME, JSON.stringify(event))
    localStorage.removeItem(CHANNEL_NAME)
  }
  return event
}

export function subscribeSessionMessages(
  callback: (event: SessionMessageEvent) => void,
): () => void {
  let channel: BroadcastChannel | null = null
  const accept = (value: unknown) => {
    if (!value || typeof value !== 'object') return
    const event = value as Partial<SessionMessageEvent>
    if (
      typeof event.eventId !== 'string' ||
      typeof event.conversationId !== 'string' ||
      typeof event.text !== 'string' ||
      !['user', 'assistant', 'system'].includes(String(event.role))
    ) return
    callback(event as SessionMessageEvent)
  }
  try {
    channel = new BroadcastChannel(CHANNEL_NAME)
    channel.onmessage = (message) => accept(message.data)
  } catch {
    channel = null
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key !== CHANNEL_NAME || !event.newValue) return
    try { accept(JSON.parse(event.newValue)) } catch { /* ignore malformed fallback events */ }
  }
  window.addEventListener('storage', onStorage)
  return () => {
    channel?.close()
    window.removeEventListener('storage', onStorage)
  }
}
