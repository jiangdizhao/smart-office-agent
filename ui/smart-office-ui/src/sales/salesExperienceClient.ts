import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VisitLease } from '../vision/visitLeaseRegistry'
import type { VoiceDeliveryPlan } from './voiceDelivery'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const TIMEOUT_MS = 5_000

export type SalesExperienceReply = {
  schema_version: 'sales-experience-reply-v1'
  conversation_id: string
  visit_id: string
  language: VoiceLanguage
  reply_mode: 'opening' | 'proactive_sales'
  purpose: 'sales_opening' | 'sales_proactive_first' | 'sales_proactive_second'
  text: string
  fallback_text: string
  expect_user_response: boolean
  question_field: string | null
  humour_theme: string | null
  delivery: VoiceDeliveryPlan
}

export type SalesExperienceProactiveResponse = {
  ok: boolean
  phase: 'phase1_sales_experience'
  speak: boolean
  reason: string
  reply: SalesExperienceReply | null
  session: {
    proactive_nudge_count: number
    stage: string
  }
}

async function postJson<T>(
  path: string,
  body: unknown,
  lease: VisitLease | null,
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
  const onAbort = () => controller.abort()
  lease?.signal.addEventListener('abort', onAbort, { once: true })
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    if (!response.ok) {
      throw new Error(`Sales experience API failed: ${response.status} ${await response.text()}`)
    }
    return (await response.json()) as T
  } finally {
    window.clearTimeout(timer)
    lease?.signal.removeEventListener('abort', onAbort)
  }
}

export async function requestSalesExperienceProactive(input: {
  conversationId: string
  visitId: string
  language: VoiceLanguage
  userSpeaking: boolean
  agentSpeaking: boolean
  toolActive: boolean
  interactionInputActive: boolean
  lease: VisitLease
}): Promise<SalesExperienceProactiveResponse> {
  return await postJson<SalesExperienceProactiveResponse>(
    '/api/sales/experience/proactive',
    {
      conversation_id: input.conversationId,
      visit_id: input.visitId,
      language: input.language,
      user_speaking: input.userSpeaking,
      agent_speaking: input.agentSpeaking,
      tool_active: input.toolActive,
      interaction_input_active: input.interactionInputActive,
    },
    input.lease,
  )
}

export async function reportSalesExperienceOutput(input: {
  conversationId: string
  visitId: string
  result: 'completed' | 'interrupted' | 'failed' | 'cancelled'
  cancelReason?: string | null
  lease: VisitLease | null
}): Promise<void> {
  await postJson(
    '/api/sales/experience/output-result',
    {
      conversation_id: input.conversationId,
      visit_id: input.visitId,
      result: input.result,
      cancel_reason: input.cancelReason ?? null,
    },
    input.lease,
  )
}
