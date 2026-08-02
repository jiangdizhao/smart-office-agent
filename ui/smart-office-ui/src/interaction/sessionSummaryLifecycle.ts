import {
  subscribeSessionMessages,
  type SessionMessageEvent,
} from './sessionEventBus'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const DEBOUNCE_MS = 1_500
const MAX_MESSAGES = 64

type SummaryMessage = {
  role: 'user' | 'assistant' | 'system'
  text: string
  timestamp?: string
  source?: string
}

type VisitBuffer = {
  conversationId: string
  visitId: string
  messages: SummaryMessage[]
  revision: number
  timer: number | null
  saving: boolean
  dirty: boolean
  finalRequested: boolean
}

const buffers = new Map<string, VisitBuffer>()
let installed = false

function bufferKey(conversationId: string, visitId: string): string {
  return `${conversationId}::${visitId}`
}

function meaningful(event: SessionMessageEvent): boolean {
  const text = event.text.trim()
  if (!text) return false
  if (/本轮未完成|需要重新尝试|voice service did not complete/i.test(text)) return false
  return event.role === 'user' || event.role === 'assistant'
}

function ensureBuffer(event: SessionMessageEvent): VisitBuffer | null {
  const conversationId = event.conversationId.trim()
  const visitId = String(event.visitId ?? '').trim()
  if (!conversationId || !visitId) return null
  const key = bufferKey(conversationId, visitId)
  let value = buffers.get(key)
  if (!value) {
    value = {
      conversationId,
      visitId,
      messages: [],
      revision: 0,
      timer: null,
      saving: false,
      dirty: false,
      finalRequested: false,
    }
    buffers.set(key, value)
  }
  return value
}

async function persist(buffer: VisitBuffer, status: 'draft' | 'final'): Promise<void> {
  if (buffer.saving) {
    buffer.dirty = true
    if (status === 'final') buffer.finalRequested = true
    return
  }
  buffer.saving = true
  buffer.dirty = false
  const revision = buffer.revision
  try {
    const response = await fetch(`${API_BASE_URL}/api/visitor-experience/session-summaries`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: buffer.conversationId,
        visit_id: buffer.visitId,
        language: 'zh',
        status,
        messages: buffer.messages,
        source_revision: revision,
      }),
      keepalive: status === 'final',
    })
    if (!response.ok) {
      throw new Error(`Session summary persistence failed: ${response.status}`)
    }
    window.dispatchEvent(new CustomEvent('smartoffice:session-summary-updated', {
      detail: {
        conversationId: buffer.conversationId,
        visitId: buffer.visitId,
        revision,
        status,
      },
    }))
  } catch (error) {
    console.warn('[SessionSummary] persistence-failed', {
      conversationId: buffer.conversationId,
      visitId: buffer.visitId,
      status,
      message: error instanceof Error ? error.message : String(error),
    })
  } finally {
    buffer.saving = false
    if (status === 'final') {
      buffers.delete(bufferKey(buffer.conversationId, buffer.visitId))
      return
    }
    if (buffer.finalRequested) {
      buffer.finalRequested = false
      void persist(buffer, 'final')
      return
    }
    if (buffer.dirty) schedule(buffer)
  }
}

function schedule(buffer: VisitBuffer): void {
  if (buffer.finalRequested) return
  if (buffer.timer !== null) window.clearTimeout(buffer.timer)
  buffer.timer = window.setTimeout(() => {
    buffer.timer = null
    void persist(buffer, 'draft')
  }, DEBOUNCE_MS)
}

function onMessage(event: SessionMessageEvent): void {
  if (!meaningful(event)) return
  const buffer = ensureBuffer(event)
  if (!buffer || buffer.finalRequested) return
  const duplicate = buffer.messages.some((item) =>
    item.role === event.role
    && item.text === event.text.trim()
    && item.timestamp === event.timestamp,
  )
  if (duplicate) return
  buffer.messages.push({
    role: event.role,
    text: event.text.trim(),
    timestamp: event.timestamp,
    source: event.source,
  })
  if (buffer.messages.length > MAX_MESSAGES) {
    buffer.messages.splice(0, buffer.messages.length - MAX_MESSAGES)
  }
  buffer.revision += 1
  schedule(buffer)
}

function finaliseVisitId(visitId: string): void {
  if (!visitId) return
  for (const buffer of buffers.values()) {
    if (buffer.visitId !== visitId) continue
    if (buffer.timer !== null) window.clearTimeout(buffer.timer)
    buffer.timer = null
    buffer.finalRequested = true
    if (!buffer.saving) {
      buffer.finalRequested = false
      void persist(buffer, 'final')
    }
  }
}

function finaliseVisit(event: Event): void {
  const detail = event instanceof CustomEvent ? event.detail : null
  const visitId = String(detail?.visitId ?? detail?.endedVisitId ?? '').trim()
  finaliseVisitId(visitId)
}

export function installSessionSummaryLifecycle(): void {
  if (installed) return
  installed = true
  subscribeSessionMessages(onMessage)
  window.addEventListener('smartoffice:visit-revoked', finaliseVisit)
  window.addEventListener('smartoffice:visit-activated', (event: Event) => {
    const detail = event instanceof CustomEvent ? event.detail : null
    const activeVisitId = String(detail?.visitId ?? '').trim()
    const replacedVisitId = String(detail?.replacedVisitId ?? '').trim()
    if (replacedVisitId && replacedVisitId !== activeVisitId) {
      finaliseVisitId(replacedVisitId)
    }
  })
}

installSessionSummaryLifecycle()
