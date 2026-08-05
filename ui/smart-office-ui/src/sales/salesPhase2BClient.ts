import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VisitLease } from '../vision/visitLeaseRegistry'
import type { VoiceDeliveryPlan } from './voiceDelivery'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const REQUEST_TIMEOUT_MS = 8_000

export type Phase2BInteractionKind = 'meeting' | 'contact' | 'recording' | 'transcript' | 'results'
export type Phase2BInteractionResult = 'opened' | 'submitted' | 'failed' | 'cancelled' | 'closed'

export type Phase2BReply = {
  schema_version: 'sales-phase2b-reply-v1'
  conversation_id: string
  visit_id: string
  language: VoiceLanguage
  reply_mode: 'engagement' | 'proactive_sales' | 'conversion' | 'closing'
  purpose: string
  text: string
  fallback_text: string
  expect_user_response: boolean
  question_field: string | null
  humour_theme: string | null
  recommended_action: string | null
  delivery: VoiceDeliveryPlan
}

export type Phase2BStatus = {
  phase: 'phase2b_engagement_conversion'
  stage: string
  episode_id: string | null
  episode_nudge_count: number
  total_nudge_count: number
  active: boolean
  current_topic: string | null
  current_industry: string | null
  last_plan_reason: string | null
  last_recommendation: string | null
  recommendation_count: number
  interaction_kind: Phase2BInteractionKind | null
  last_interaction_result: Phase2BInteractionResult | null
  conversion_state: string
  busy_retry_count: number
  timing: {
    first_idle_seconds: number
    second_idle_seconds: number
    busy_retry_seconds: number
    after_interaction_seconds: number
  }
  limits: {
    maximum_nudges_per_episode: number
    maximum_nudges_per_visit: number
  }
}

export type Phase2BProactiveResponse = {
  ok: boolean
  phase: 'phase2b_engagement_conversion'
  speak: boolean
  defer: boolean
  retry_after_ms: number | null
  reason: string
  reply: Phase2BReply | null
  engagement: Phase2BStatus
  session: Record<string, unknown>
}

type JsonObject = Record<string, unknown>

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

async function postJson<T>(
  path: string,
  body: JsonObject,
  lease?: VisitLease | null,
): Promise<T> {
  if (lease?.signal.aborted) throw abortError('Phase 2B request belongs to a stale Visit.')
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
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
      throw new Error(`Phase 2B request failed: ${response.status} ${await response.text()}`)
    }
    return await response.json() as T
  } catch (error) {
    if (lease?.signal.aborted) throw abortError('Phase 2B request belongs to a stale Visit.')
    throw error
  } finally {
    window.clearTimeout(timer)
    lease?.signal.removeEventListener('abort', onAbort)
  }
}

export async function observePhase2BTurn(input: {
  conversationId: string
  visitId: string
  language: VoiceLanguage
  text: string
  recentContext?: string
  lease?: VisitLease | null
}): Promise<{ engagement: Phase2BStatus; session: Record<string, unknown> }> {
  return await postJson('/api/sales/phase2b/observe-turn', {
    conversation_id: input.conversationId,
    visit_id: input.visitId,
    language: input.language,
    text: input.text,
    recent_context: input.recentContext ?? '',
  }, input.lease)
}

export async function reportPhase2BOutput(input: {
  conversationId: string
  visitId: string
  result: 'completed' | 'interrupted' | 'failed' | 'cancelled'
  purpose: string
  replyMode: string
  expectUserResponse: boolean
  questionField: string | null
  text: string
  cancelReason?: string | null
  lease?: VisitLease | null
}): Promise<{ engagement: Phase2BStatus; session: Record<string, unknown> }> {
  return await postJson('/api/sales/phase2b/output-result', {
    conversation_id: input.conversationId,
    visit_id: input.visitId,
    result: input.result,
    purpose: input.purpose,
    reply_mode: input.replyMode,
    expect_user_response: input.expectUserResponse,
    question_field: input.questionField,
    text: input.text,
    cancel_reason: input.cancelReason ?? null,
  }, input.lease)
}

export async function reportPhase2BInteraction(input: {
  conversationId: string
  visitId: string
  kind: Phase2BInteractionKind
  result: Phase2BInteractionResult
  verified?: boolean
  message?: string
  data?: Record<string, unknown>
  lease?: VisitLease | null
}): Promise<{ engagement: Phase2BStatus; session: Record<string, unknown> }> {
  return await postJson('/api/sales/phase2b/interaction-result', {
    conversation_id: input.conversationId,
    visit_id: input.visitId,
    kind: input.kind,
    result: input.result,
    verified: input.verified ?? false,
    message: input.message ?? '',
    data: input.data ?? {},
  }, input.lease)
}

export async function requestPhase2BProactive(input: {
  conversationId: string
  visitId: string
  language: VoiceLanguage
  userSpeaking: boolean
  agentSpeaking: boolean
  toolActive: boolean
  interactionInputActive: boolean
  lease?: VisitLease | null
}): Promise<Phase2BProactiveResponse> {
  return await postJson('/api/sales/phase2b/proactive', {
    conversation_id: input.conversationId,
    visit_id: input.visitId,
    language: input.language,
    user_speaking: input.userSpeaking,
    agent_speaking: input.agentSpeaking,
    tool_active: input.toolActive,
    interaction_input_active: input.interactionInputActive,
  }, input.lease)
}

export async function endPhase2BVisit(input: {
  conversationId: string
  visitId: string
}): Promise<void> {
  if (!input.conversationId || !input.visitId) return
  await fetch(
    `${API_BASE_URL}/api/sales/phase2b/visit/${encodeURIComponent(input.conversationId)}/${encodeURIComponent(input.visitId)}`,
    { method: 'DELETE', keepalive: true },
  ).catch(() => undefined)
}
