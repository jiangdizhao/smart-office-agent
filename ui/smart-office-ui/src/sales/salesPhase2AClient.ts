import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VisitLease } from '../vision/visitLeaseRegistry'
import type { VoiceDeliveryPlan } from './voiceDelivery'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const TIMEOUT_MS = 5_000

export type Phase2AProactiveReply = {
  schema_version: 'sales-phase2a-reply-v1'
  conversation_id: string
  visit_id: string
  language: VoiceLanguage
  reply_mode: 'proactive_sales'
  purpose: 'sales_phase2a_proactive_first' | 'sales_phase2a_proactive_second'
  text: string
  fallback_text: string
  expect_user_response: boolean
  question_field: string | null
  humour_theme: null
  delivery: VoiceDeliveryPlan
}

export type Phase2AProactiveResponse = {
  ok: boolean
  phase: 'phase2a_sales_continuity'
  speak: boolean
  reason: string
  reply: Phase2AProactiveReply | null
  session: Record<string, unknown>
  continuity: {
    episode_id?: string | null
    episode_nudge_count?: number
    total_nudge_count?: number
    active?: boolean
    last_planned_action?: string | null
    maximum_nudges_per_episode?: number
    maximum_nudges_per_visit?: number
  }
}

export type Phase2AOutputLifecycle = {
  conversationId: string
  visitId: string
  result: 'completed' | 'interrupted' | 'failed' | 'cancelled'
  purpose: string
  replyMode: string
  expectUserResponse: boolean
  questionField: string | null
  text: string
  cancelReason?: string | null
  lease: VisitLease | null
}

async function fetchWithLease<T>(
  path: string,
  init: RequestInit,
  lease: VisitLease | null,
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
  const onAbort = () => controller.abort()
  lease?.signal.addEventListener('abort', onAbort, { once: true })
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
    })
    if (!response.ok) {
      throw new Error(`Phase 2A API failed: ${response.status} ${await response.text()}`)
    }
    return (await response.json()) as T
  } finally {
    window.clearTimeout(timer)
    lease?.signal.removeEventListener('abort', onAbort)
  }
}

export async function fetchPhase2ASelfIntroduction(
  language: VoiceLanguage,
  lease: VisitLease | null,
): Promise<string> {
  const payload = await fetchWithLease<{ text?: string }>(
    `/api/sales/phase2a/self-introduction?language=${encodeURIComponent(language)}`,
    { headers: { Accept: 'application/json' } },
    lease,
  )
  const text = String(payload.text ?? '').trim()
  if (!text) throw new Error('The Phase 2A self-introduction is empty.')
  return text
}

export async function requestPhase2AProactive(input: {
  conversationId: string
  visitId: string
  language: VoiceLanguage
  userSpeaking: boolean
  agentSpeaking: boolean
  toolActive: boolean
  interactionInputActive: boolean
  lease: VisitLease
}): Promise<Phase2AProactiveResponse> {
  return await fetchWithLease<Phase2AProactiveResponse>(
    '/api/sales/phase2a/proactive',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: input.conversationId,
        visit_id: input.visitId,
        language: input.language,
        user_speaking: input.userSpeaking,
        agent_speaking: input.agentSpeaking,
        tool_active: input.toolActive,
        interaction_input_active: input.interactionInputActive,
      }),
    },
    input.lease,
  )
}

export async function reportPhase2AOutput(input: Phase2AOutputLifecycle): Promise<void> {
  await fetchWithLease(
    '/api/sales/phase2a/output-result',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: input.conversationId,
        visit_id: input.visitId,
        result: input.result,
        purpose: input.purpose,
        reply_mode: input.replyMode,
        expect_user_response: input.expectUserResponse,
        question_field: input.questionField,
        text: input.text,
        cancel_reason: input.cancelReason ?? null,
      }),
    },
    input.lease,
  )
}
