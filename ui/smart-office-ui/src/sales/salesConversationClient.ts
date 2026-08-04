import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VisitLease } from '../vision/visitLeaseRegistry'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const SALES_TIMEOUT_MS = 5_000

export type SalesUiAction = 'open_booking' | 'open_contact'

export type SalesHumourDirective = {
  allowed: boolean
  intensity: 'none' | 'light'
  theme: string | null
  text: string | null
  reason: string
  maximum_lines: number
  forbidden_topics: string[]
}

export type SalesReplyPlan = {
  schema_version: 'sales-reply-plan-v1'
  conversation_id: string
  visit_id: string
  language: VoiceLanguage
  reply_mode:
    | 'pure_sales'
    | 'hybrid_sales'
    | 'exact_operational'
    | 'proactive_sales'
    | 'closing'
  sales_stage:
    | 'attract'
    | 'discover'
    | 'recommend'
    | 'demonstrate'
    | 'handle_objection'
    | 'convert'
    | 'close'
  goal: string
  verified_facts: string[]
  approved_claims: string[]
  prohibited_claims: string[]
  visitor_context: string[]
  capability_ids: string[]
  suggested_question: string | null
  recommended_action: string | null
  humour: SalesHumourDirective
  maximum_sentences: number
  created_at: string
}

export type SalesSessionState = {
  conversation_id: string
  visit_id: string
  language: VoiceLanguage
  stage: SalesReplyPlan['sales_stage']
  turn_count: number
  effective_user_turn_count: number
  explicit_facts: Record<string, string>
  pain_points: string[]
  interested_capabilities: string[]
  demonstrated_capabilities: string[]
  explicit_demo_request_counts: Record<string, number>
  asked_fields: string[]
  declined_fields: string[]
  objections: string[]
  booking_offer_count: number
  contact_offer_count: number
  booking_rejected: boolean
  contact_rejected: boolean
  booking_opened: boolean
  contact_opened: boolean
  value_delivered: boolean
  cost_claim_used_count: number
  humour_used_count: number
  humour_themes_used: string[]
  turns_since_humour: number
  proactive_nudge_count: number
  last_recommended_capability: string | null
  last_sales_action: string | null
  profile_persisted: boolean
  disengaged: boolean
}

export type SalesTurnResponse = {
  ok: boolean
  phase: 'phase1_sales_runtime'
  handled: boolean
  route: 'sales_realtime' | 'pass_through'
  reason: string
  extraction: {
    demo_capability_id: string | null
    explicit_demo_request: boolean
    direct_operational_command: boolean
    sales_relevant: boolean
  } & Record<string, unknown>
  reply_plan: SalesReplyPlan | null
  fallback_text: string
  ui_action: SalesUiAction | null
  session: SalesSessionState
  profile_persisted: boolean
}

export type SalesProactiveResponse = {
  ok: boolean
  phase: 'phase1_sales_runtime'
  speak: boolean
  reason: string
  reply_plan: SalesReplyPlan | null
  fallback_text: string
  session: SalesSessionState
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

async function postJson<T>(
  path: string,
  body: unknown,
  lease: VisitLease | null,
  timeoutMs = SALES_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
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
      throw new Error(`Sales API failed: ${response.status} ${await response.text()}`)
    }
    return (await response.json()) as T
  } catch (error) {
    if (lease?.signal.aborted) throw abortError('The sales request belongs to a stale Visit.')
    throw error
  } finally {
    window.clearTimeout(timer)
    lease?.signal.removeEventListener('abort', onAbort)
  }
}

export async function previewSalesTurn(input: {
  conversationId: string
  visitId: string
  text: string
  language: VoiceLanguage
  actor: 'visitor' | 'employee' | 'operator'
  recentContext: string
  semanticExtraction?: Record<string, unknown> | null
  lease: VisitLease | null
}): Promise<SalesTurnResponse> {
  return await postJson<SalesTurnResponse>(
    '/api/sales/turn',
    {
      conversation_id: input.conversationId,
      visit_id: input.visitId,
      text: input.text,
      language: input.language,
      actor_type: input.actor,
      recent_context: input.recentContext,
      semantic_extraction: input.semanticExtraction ?? null,
    },
    input.lease,
  )
}

export async function requestSalesProactive(input: {
  conversationId: string
  visitId: string
  language: VoiceLanguage
  silenceSeconds: number
  userSpeaking: boolean
  agentSpeaking: boolean
  toolActive: boolean
  interactionInputActive: boolean
  lease: VisitLease | null
}): Promise<SalesProactiveResponse> {
  return await postJson<SalesProactiveResponse>(
    '/api/sales/proactive',
    {
      conversation_id: input.conversationId,
      visit_id: input.visitId,
      language: input.language,
      silence_seconds: Math.max(1, Math.round(input.silenceSeconds)),
      user_speaking: input.userSpeaking,
      agent_speaking: input.agentSpeaking,
      tool_active: input.toolActive,
      interaction_input_active: input.interactionInputActive,
    },
    input.lease,
  )
}

export async function reportSalesConversionEvent(input: {
  conversationId: string
  visitId: string
  event: 'booking_opened' | 'booking_failed' | 'contact_opened' | 'contact_failed'
  lease: VisitLease | null
}): Promise<void> {
  await postJson(
    '/api/sales/conversion-event',
    {
      conversation_id: input.conversationId,
      visit_id: input.visitId,
      event: input.event,
    },
    input.lease,
  )
}

export async function endSalesVisit(input: {
  conversationId: string
  visitId: string
}): Promise<void> {
  await fetch(`${API_BASE_URL}/api/sales/visit/end`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({
      conversation_id: input.conversationId,
      visit_id: input.visitId,
    }),
    keepalive: true,
  }).catch(() => undefined)
}
