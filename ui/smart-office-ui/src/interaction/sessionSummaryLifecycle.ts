import {
  subscribeSessionMessages,
  type SessionMessageEvent,
} from './sessionEventBus'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const DEBOUNCE_MS = 1_500
const DRAFT_LLM_IDLE_MS = 4_000
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
  llmTimer: number | null
  saving: boolean
  llmSaving: boolean
  dirty: boolean
  llmDirty: boolean
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
      llmTimer: null,
      saving: false,
      llmSaving: false,
      dirty: false,
      llmDirty: false,
      finalRequested: false,
    }
    buffers.set(key, value)
  }
  return value
}

function summaryLanguage(messages: SummaryMessage[]): 'zh' | 'en' {
  const userText = messages
    .filter((item) => item.role === 'user')
    .map((item) => item.text)
    .join(' ')
  return /[\u3400-\u9fff]/.test(userText) ? 'zh' : 'en'
}

function summaryBody(buffer: VisitBuffer, status: 'draft' | 'final', revision: number): string {
  return JSON.stringify({
    conversation_id: buffer.conversationId,
    visit_id: buffer.visitId,
    language: summaryLanguage(buffer.messages),
    status,
    messages: buffer.messages,
    source_revision: revision,
  })
}

function dispatchUpdated(
  buffer: VisitBuffer,
  revision: number,
  status: 'draft' | 'final',
  summaryMode: string | null,
): void {
  window.dispatchEvent(new CustomEvent('smartoffice:session-summary-updated', {
    detail: {
      conversationId: buffer.conversationId,
      visitId: buffer.visitId,
      revision,
      status,
      summaryMode,
    },
  }))
}

async function synthesiseDraft(buffer: VisitBuffer): Promise<void> {
  if (buffer.finalRequested) return
  if (buffer.llmSaving) {
    buffer.llmDirty = true
    return
  }
  buffer.llmSaving = true
  buffer.llmDirty = false
  const revision = buffer.revision
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/visitor-experience/session-summaries/llm-draft`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: summaryBody(buffer, 'draft', revision),
      },
    )
    if (!response.ok) {
      throw new Error(`LLM draft summary failed: ${response.status}`)
    }
    const payload = await response.json().catch(() => null) as {
      summary_mode?: string
      summary?: { summary_mode?: string }
    } | null
    dispatchUpdated(
      buffer,
      revision,
      'draft',
      payload?.summary?.summary_mode ?? payload?.summary_mode ?? null,
    )
  } catch (error) {
    console.warn('[SessionSummary] llm-draft-failed', {
      conversationId: buffer.conversationId,
      visitId: buffer.visitId,
      revision,
      message: error instanceof Error ? error.message : String(error),
    })
  } finally {
    buffer.llmSaving = false
    if (buffer.finalRequested) {
      if (!buffer.saving) {
        buffer.finalRequested = false
        void persist(buffer, 'final')
      }
      return
    }
    if (buffer.llmDirty || buffer.revision !== revision) scheduleLlm(buffer)
  }
}

async function persist(buffer: VisitBuffer, status: 'draft' | 'final'): Promise<void> {
  if (status === 'final' && buffer.llmTimer !== null) {
    window.clearTimeout(buffer.llmTimer)
    buffer.llmTimer = null
  }
  if (buffer.saving || (status === 'final' && buffer.llmSaving)) {
    buffer.dirty = true
    if (status === 'final') buffer.finalRequested = true
    return
  }
  buffer.saving = true
  buffer.dirty = false
  const revision = buffer.revision
  const endpoint = status === 'final'
    ? '/api/visitor-experience/session-summaries/llm'
    : '/api/visitor-experience/session-summaries'
  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: summaryBody(buffer, status, revision),
      // The main Virtual Host page normally remains open while the Visit changes.
      // Keepalive is only useful for the small deterministic draft request; the final
      // LLM request is allowed to finish normally so its response is not size-limited.
      keepalive: status === 'draft',
    })
    if (!response.ok) {
      throw new Error(`Session summary persistence failed: ${response.status}`)
    }
    const payload = await response.json().catch(() => null) as {
      summary_mode?: string
      summary?: { summary_mode?: string }
    } | null
    dispatchUpdated(
      buffer,
      revision,
      status,
      payload?.summary?.summary_mode ?? payload?.summary_mode ?? null,
    )
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
    if (buffer.finalRequested && !buffer.llmSaving) {
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

function scheduleLlm(buffer: VisitBuffer): void {
  if (buffer.finalRequested) return
  if (buffer.llmTimer !== null) window.clearTimeout(buffer.llmTimer)
  buffer.llmTimer = window.setTimeout(() => {
    buffer.llmTimer = null
    void synthesiseDraft(buffer)
  }, DRAFT_LLM_IDLE_MS)
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
  scheduleLlm(buffer)
}

function finaliseVisitId(visitId: string): void {
  if (!visitId) return
  for (const buffer of buffers.values()) {
    if (buffer.visitId !== visitId) continue
    if (buffer.timer !== null) window.clearTimeout(buffer.timer)
    if (buffer.llmTimer !== null) window.clearTimeout(buffer.llmTimer)
    buffer.timer = null
    buffer.llmTimer = null
    buffer.finalRequested = true
    if (!buffer.saving && !buffer.llmSaving) {
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
