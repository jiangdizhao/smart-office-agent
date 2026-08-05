import {
  realtimeOfficeInterpreter,
  type RealtimeOfficeDecision,
  type RealtimeOfficeToolCall,
} from './realtimeOfficeInterpreter'
import type {
  SemanticAction,
  UnifiedSemanticRouteResponse,
} from '../routing/unifiedSemanticRouterClient'
import { installOfficeTaskEventObserver } from './officeTaskEventObserver'
import { installConversationWriteBehind } from './conversationWriteBehind'

const SLOW_PRESENTATION_STEPS = new Set([
  'presentation_open_configured',
  'presentation_start_slideshow',
  'presentation_close',
])

declare global {
  interface Window {
    __SMART_OFFICE_SEMANTIC_ROUTE__?: UnifiedSemanticRouteResponse
    __SMART_OFFICE_SEMANTIC_OFFICE_BRIDGE_INSTALLED__?: boolean
  }
}

function normalized(value: string): string {
  return value
    .normalize('NFKC')
    .toLocaleLowerCase()
    .replace(/[，。！？、;；:：,.!?\s]+/g, '')
    .trim()
}

function evidenceMatches(text: string, action: SemanticAction): boolean {
  const source = normalized(text)
  const evidence = normalized(action.evidence ?? '')
  return Boolean(source && evidence && (source.includes(evidence) || evidence.includes(source)))
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : null
}

function semanticStep(action: SemanticAction): Record<string, unknown> | null {
  if (action.polarity !== 'affirmed') return null
  if (action.target === 'powerpoint') {
    if (action.verb === 'go_to') {
      const slideNumber = numeric(action.arguments.slide_number)
      if (slideNumber !== null && slideNumber >= 1) {
        return { name: 'presentation_go_to_slide', slide_number: slideNumber }
      }
      if (action.arguments.slide_target === 'last') {
        return { name: 'presentation_go_to_slide', slide_target: 'last' }
      }
      return null
    }
    const mapping: Record<string, string> = {
      open: 'presentation_open_configured',
      start: 'presentation_start_slideshow',
      next: 'presentation_next_slide',
      previous: 'presentation_previous_slide',
      stop: 'presentation_end_slideshow',
      close: 'presentation_close',
      status: 'presentation_get_status',
      get: 'presentation_get_status',
    }
    const name = mapping[action.verb]
    return name ? { name } : null
  }

  if (action.target === 'system_volume') {
    if (action.verb === 'set') {
      const percent = numeric(action.arguments.percent ?? action.arguments.value_percent)
      if (percent !== null && percent >= 0 && percent <= 100) {
        return { name: 'system_set_volume', value_percent: percent }
      }
    }
    if (action.verb === 'adjust') {
      const delta = numeric(action.arguments.delta_percent)
      if (delta !== null && delta !== 0 && delta >= -100 && delta <= 100) {
        return { name: 'system_adjust_volume', delta_percent: delta }
      }
    }
    if (['get', 'status'].includes(action.verb)) return { name: 'system_get_status' }
  }
  return null
}

function toolCall(steps: Array<Record<string, unknown>>): RealtimeOfficeToolCall {
  return {
    name: 'office_plan',
    arguments: { steps },
    call_id: null,
    source: 'gpt_realtime',
  }
}

function makeSlowPresentationNonBlocking(
  decision: RealtimeOfficeDecision,
): RealtimeOfficeDecision {
  if (decision.kind !== 'tool_call') return decision
  const steps = decision.toolCall.arguments.steps
  if (!Array.isArray(steps) || steps.length !== 1) return decision
  const first = steps[0]
  if (!first || typeof first !== 'object' || Array.isArray(first)) return decision
  const name = String((first as Record<string, unknown>).name ?? '')
  if (!SLOW_PRESENTATION_STEPS.has(name)) return decision

  // The Backend schedules plans with two or more steps as background tasks. Adding
  // an explicit status observation preserves verification while allowing the
  // conversation lane to acknowledge immediately and continue independently.
  return {
    kind: 'tool_call',
    toolCall: {
      ...decision.toolCall,
      arguments: {
        ...decision.toolCall.arguments,
        steps: [first, { name: 'presentation_get_status' }],
      },
    },
  }
}

function decisionFromLatestSemanticRoute(text: string): RealtimeOfficeDecision | null {
  const semantic = window.__SMART_OFFICE_SEMANTIC_ROUTE__
  if (
    !semantic
    || semantic.final_policy_decision !== 'execute'
    || semantic.route.domain !== 'office'
    || semantic.route.actions.length === 0
  ) return null

  if (!semantic.route.actions.every((action) => evidenceMatches(text, action))) return null
  const steps = semantic.route.actions.map(semanticStep)
  if (steps.some((step) => step === null)) return null
  const cleanSteps = steps.filter((step): step is Record<string, unknown> => step !== null)
  if (!cleanSteps.length) return null
  return makeSlowPresentationNonBlocking({ kind: 'tool_call', toolCall: toolCall(cleanSteps) })
}

export function installSemanticOfficeInterpreterBridge(): void {
  installOfficeTaskEventObserver()
  installConversationWriteBehind()
  if (window.__SMART_OFFICE_SEMANTIC_OFFICE_BRIDGE_INSTALLED__) return
  window.__SMART_OFFICE_SEMANTIC_OFFICE_BRIDGE_INSTALLED__ = true
  const originalInterpret = realtimeOfficeInterpreter.interpret.bind(realtimeOfficeInterpreter)

  realtimeOfficeInterpreter.interpret = async (text, language) => {
    const semanticDecision = decisionFromLatestSemanticRoute(text)
    if (semanticDecision) {
      console.info('[ConversationLatency] semantic-office-plan-reused', {
        textLength: text.length,
        decisionKind: semanticDecision.kind,
      })
      return semanticDecision
    }
    const startedAt = performance.now()
    const originalDecision = await originalInterpret(text, language)
    const decision = makeSlowPresentationNonBlocking(originalDecision)
    console.info('[ConversationLatency] office-interpreter-fallback-complete', {
      elapsedMs: Math.round(performance.now() - startedAt),
      backgroundPresentationPromoted: decision !== originalDecision,
      decisionKind: decision.kind,
    })
    return decision
  }
}
