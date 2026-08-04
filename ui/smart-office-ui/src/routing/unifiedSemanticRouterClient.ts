import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VisitLease } from '../vision/visitLeaseRegistry'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const ROUTER_TIMEOUT_MS = 15_000
const SUPPORTED_SALES_PROFILE_FIELDS = new Set([
  'industry',
  'role',
  'company_type',
  'office_pain_points',
  'interested_capabilities',
])

export type SemanticDomain =
  | 'office'
  | 'interaction'
  | 'sales'
  | 'general'
  | 'identity'
  | 'privacy'
  | 'unknown'

export type SemanticActionMode =
  | 'execute'
  | 'answer_only'
  | 'clarify'
  | 'request_confirmation'
  | 'reject'
  | 'delegate'

export type SemanticAction = {
  verb: string
  target: string
  arguments: Record<string, unknown>
  polarity: 'affirmed' | 'negated' | 'uncertain'
  speech_act:
    | 'command'
    | 'question'
    | 'explanation_request'
    | 'hypothetical'
    | 'quotation'
    | 'conditional'
    | 'response'
    | 'statement'
  evidence: string
  sequence: number
}

export type SemanticEvidence = {
  value: string
  evidence: string
}

export type SemanticProfileExtraction = {
  explicit_facts: Record<string, SemanticEvidence>
  pain_points: SemanticEvidence[]
  interested_capabilities: SemanticEvidence[]
  objections: SemanticEvidence[]
  declined_fields: string[]
  demo_capability_id: string | null
  explicit_demo_request: boolean
  booking_intent: 'none' | 'accept' | 'reject' | 'direct'
  contact_intent: 'none' | 'accept' | 'reject' | 'direct'
  cost_question: boolean
  privacy_question: boolean
  ordinary_chatbot_objection: boolean
  disengaged: boolean
  brief_affirmation: boolean
  brief_rejection: boolean
  direct_operational_command: boolean
  sales_relevant: boolean
}

export type UnifiedSemanticRoute = {
  schema_version: 'semantic-route-v1'
  primary_intent: string
  domain: SemanticDomain
  action_mode: SemanticActionMode
  confidence: number
  requires_clarification: boolean
  clarification_question: string | null
  actions: SemanticAction[]
  negated_actions: SemanticAction[]
  entities: Record<string, unknown>
  sales_signals: string[]
  conversation_reference: string | null
  risk: 'none' | 'low' | 'business_state' | 'external_effect'
  reason_codes: string[]
  profile_extraction: SemanticProfileExtraction
  complexity: 'simple' | 'complex' | 'not_applicable'
  answer_engine: 'realtime' | 'terra' | 'office_interpreter' | 'backend'
  source: 'fast_path' | 'semantic_model' | 'pending_intent' | 'safe_fallback'
}

export type UnifiedSemanticRouteResponse = {
  ok: boolean
  phase: 'unified_semantic_routing'
  mode: 'legacy' | 'shadow' | 'unified'
  route: UnifiedSemanticRoute
  final_policy_decision: SemanticActionMode
  policy_reason_codes: string[]
  pending_intent_used: string | null
  legacy_comparison: Record<string, unknown> | null
  decision_id: string
  model: string | null
  elapsed_ms: number
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

async function fetchWithTimeout(
  path: string,
  init: RequestInit,
  lease: VisitLease | null,
  timeoutMs = ROUTER_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  const onAbort = () => controller.abort()
  lease?.signal.addEventListener('abort', onAbort, { once: true })
  try {
    return await fetch(`${API_BASE_URL}${path}`, { ...init, signal: controller.signal })
  } catch (error) {
    if (lease?.signal.aborted) throw abortError('Semantic route belongs to a stale Visit.')
    throw error
  } finally {
    window.clearTimeout(timer)
    lease?.signal.removeEventListener('abort', onAbort)
  }
}

export async function requestUnifiedSemanticRoute(input: {
  conversationId: string
  visitId: string | null
  text: string
  language: VoiceLanguage
  actor: 'visitor' | 'employee' | 'operator'
  lease: VisitLease | null
  interactionPanel?: string | null
  activeTool?: string | null
}): Promise<UnifiedSemanticRouteResponse> {
  const response = await fetchWithTimeout(
    '/api/semantic-route',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: input.conversationId,
        visit_id: input.visitId,
        text: input.text,
        language: input.language,
        actor_type: input.actor,
        recent_turns: [],
        runtime_context: {
          interaction_panel: input.interactionPanel ?? null,
          active_tool: input.activeTool ?? null,
          assistant_speaking: false,
          visitor_present: Boolean(input.visitId),
        },
      }),
    },
    input.lease,
  )
  if (!response.ok) {
    throw new Error(`Unified semantic route failed: ${response.status} ${await response.text()}`)
  }
  const payload = (await response.json()) as UnifiedSemanticRouteResponse
  ;(window as Window & {
    __SMART_OFFICE_SEMANTIC_ROUTE__?: UnifiedSemanticRouteResponse
  }).__SMART_OFFICE_SEMANTIC_ROUTE__ = payload
  window.dispatchEvent(new CustomEvent('smartoffice:semantic-route-decision', { detail: payload }))
  return payload
}

export async function setSemanticPendingIntent(input: {
  conversationId: string
  visitId: string
  intentType: 'booking_offer' | 'contact_offer' | 'demo_choice' | 'confirmation' | 'clarification'
  sourceTurnId?: string | null
  metadata?: Record<string, unknown>
  lease: VisitLease | null
}): Promise<void> {
  const response = await fetchWithTimeout(
    '/api/semantic-route/pending',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: input.conversationId,
        visit_id: input.visitId,
        intent_type: input.intentType,
        source_turn_id: input.sourceTurnId ?? null,
        metadata: input.metadata ?? {},
      }),
    },
    input.lease,
    5_000,
  )
  if (!response.ok) {
    throw new Error(`Pending semantic intent failed: ${response.status} ${await response.text()}`)
  }
}

export async function clearSemanticPendingIntent(input: {
  conversationId: string
  visitId: string
  lease: VisitLease | null
}): Promise<void> {
  const path = `/api/semantic-route/pending/${encodeURIComponent(input.conversationId)}/${encodeURIComponent(input.visitId)}`
  const response = await fetchWithTimeout(
    path,
    { method: 'DELETE' },
    input.lease,
    5_000,
  )
  if (!response.ok) {
    throw new Error(`Pending semantic intent cancellation failed: ${response.status} ${await response.text()}`)
  }
}

export function flattenSemanticProfile(
  extraction: SemanticProfileExtraction,
): Record<string, unknown> {
  const explicitFacts = Object.fromEntries(
    Object.entries(extraction.explicit_facts)
      .filter(([key]) => SUPPORTED_SALES_PROFILE_FIELDS.has(key))
      .map(([key, item]) => [key, item.value]),
  )
  const declinedFields = extraction.declined_fields.filter((field) =>
    SUPPORTED_SALES_PROFILE_FIELDS.has(field),
  )
  return {
    explicit_facts: explicitFacts,
    pain_points: extraction.pain_points.map((item) => item.value),
    interested_capabilities: extraction.interested_capabilities.map((item) => item.value),
    objections: extraction.objections.map((item) => item.value),
    declined_fields: declinedFields,
    demo_capability_id: extraction.demo_capability_id,
    explicit_demo_request: extraction.explicit_demo_request,
    booking_intent: extraction.booking_intent,
    contact_intent: extraction.contact_intent,
    cost_question: extraction.cost_question,
    privacy_question: extraction.privacy_question,
    ordinary_chatbot_objection: extraction.ordinary_chatbot_objection,
    disengaged: extraction.disengaged,
    brief_affirmation: extraction.brief_affirmation,
    brief_rejection: extraction.brief_rejection,
    direct_operational_command: extraction.direct_operational_command,
    sales_relevant: extraction.sales_relevant,
    evidence: ['semantic_route_evidence_validated'],
    matched_playbooks: [],
  }
}
