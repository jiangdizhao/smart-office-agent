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

// Exhibition commands should return one verified result rather than a generic
// acknowledgement followed by a delayed task result. Compound requests remain
// background tasks naturally because they already contain two or more steps.
const SLOW_PRESENTATION_STEPS = new Set<string>()
const IDEMPOTENT_PLAN_WINDOW_MS = 8_000
const recentIdempotentPlans = new Map<string, number>()
const IDEMPOTENT_STEPS = new Set([
  'presentation_open_configured',
  'presentation_start_slideshow',
  'presentation_go_to_slide',
  'presentation_end_slideshow',
  'presentation_close',
  'system_set_volume',
  'system_set_brightness',
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
    .replace(/\bp\s*[.\-_]?\s*p\s*[.\-_]?\s*t\b/gi, 'ppt')
    .replace(/\bpower\s+point\b/gi, 'powerpoint')
    .replace(/幻\s*灯\s*片/g, '幻灯片')
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

function presentationFallback(text: string): RealtimeOfficeDecision | null {
  const source = text.normalize('NFKC').toLocaleLowerCase()
    .replace(/\bp\s*[.\-_]?\s*p\s*[.\-_]?\s*t\b/gi, 'ppt')
    .replace(/\bpower\s+point\b/gi, 'powerpoint')
    .replace(/幻\s*灯\s*片/g, '幻灯片')
    .replace(/\s+/g, ' ')
    .trim()
  const compact = normalized(source)
  if (!compact) return null
  if (/不要|别|无需|不用|只介绍|仅介绍|解释|比较|对比|假设|如果|为什么|怎么实现|如何实现|do not|don't|without|explain|compare|hypothetical/i.test(source)) {
    return null
  }

  const mentioned = /ppt|powerpoint|幻灯片|演示文稿|presentation|slides?|slideshow/i.test(source)
  const contextual = /^(下一页|上一页|下一张|上一张|后一页|前一页|最后一页|末页|开始放映|开始演示|结束放映|结束演示|nextslide|previousslide|lastslide|starttheshow|endtheshow)$/i.test(compact)
  if (!mentioned && !contextual) return null

  if (/下一页|下一张|后一页|后一张|向后翻|往后翻|next\s*slide/i.test(source)) {
    return { kind: 'tool_call', toolCall: toolCall([{ name: 'presentation_next_slide' }]) }
  }
  if (/上一页|上一张|前一页|前一张|向前翻|往前翻|previous\s*slide/i.test(source)) {
    return { kind: 'tool_call', toolCall: toolCall([{ name: 'presentation_previous_slide' }]) }
  }
  if (/最后一页|末页|last\s*slide|final\s*slide/i.test(source)) {
    return { kind: 'tool_call', toolCall: toolCall([{ name: 'presentation_go_to_slide', slide_target: 'last' }]) }
  }
  const page = source.match(/第\s*(\d+)\s*页|(?:go|jump|move)\s+to\s+slide\s+(\d+)/i)
  if (page) {
    return {
      kind: 'tool_call',
      toolCall: toolCall([{
        name: 'presentation_go_to_slide',
        slide_number: Number(page[1] ?? page[2]),
      }]),
    }
  }
  if (/结束|停止|退出|关闭放映|end|stop|exit/i.test(source) && /演示|放映|slideshow|show/i.test(source)) {
    return { kind: 'tool_call', toolCall: toolCall([{ name: 'presentation_end_slideshow' }]) }
  }
  if (/关闭|退出\s*(?:ppt|powerpoint)|close\s*(?:ppt|powerpoint)/i.test(source) && mentioned) {
    return { kind: 'tool_call', toolCall: toolCall([{ name: 'presentation_close' }]) }
  }
  if (/状态|第几页|多少页|status/i.test(source)) {
    return { kind: 'tool_call', toolCall: toolCall([{ name: 'presentation_get_status' }]) }
  }

  const open = /打开|开启|启动|调出|弄出|叫出|open|launch/i.test(source)
  const present = /播放|放映|演示|展示|全屏|运行|开始|让我看看|给我看看|做个演示|present|show|play|run|start|full\s*screen/i.test(source)
  const genericOperate = /操作|处理|动一下|试一下|体验|operate|control|demo/i.test(source)
  if ((open && present) || genericOperate || (/让我看看|给我看看/.test(source) && mentioned)) {
    return {
      kind: 'tool_call',
      toolCall: toolCall([
        { name: 'presentation_open_configured' },
        { name: 'presentation_start_slideshow' },
      ]),
    }
  }
  if (present) {
    return { kind: 'tool_call', toolCall: toolCall([{ name: 'presentation_start_slideshow' }]) }
  }
  if (open) {
    return { kind: 'tool_call', toolCall: toolCall([{ name: 'presentation_open_configured' }]) }
  }
  return null
}

function planSteps(decision: RealtimeOfficeDecision): Array<Record<string, unknown>> | null {
  if (decision.kind !== 'tool_call') return null
  const steps = decision.toolCall.arguments.steps
  if (!Array.isArray(steps)) return null
  const clean = steps.filter(
    (step): step is Record<string, unknown> => Boolean(step && typeof step === 'object' && !Array.isArray(step)),
  )
  return clean.length === steps.length ? clean : null
}

function planFingerprint(steps: Array<Record<string, unknown>>): string | null {
  if (!steps.length) return null
  const names = steps.map((step) => String(step.name ?? ''))
  if (names.some((name) => !IDEMPOTENT_STEPS.has(name))) return null
  return JSON.stringify(steps.map((step) => {
    const ordered: Record<string, unknown> = { name: step.name }
    for (const key of ['slide_number', 'slide_target', 'value_percent']) {
      if (step[key] !== undefined) ordered[key] = step[key]
    }
    return ordered
  }))
}

function suppressDuplicateIdempotentPlan(
  decision: RealtimeOfficeDecision,
): RealtimeOfficeDecision {
  const steps = planSteps(decision)
  if (!steps) return decision
  const fingerprint = planFingerprint(steps)
  if (!fingerprint) return decision

  const now = performance.now()
  for (const [key, seenAt] of recentIdempotentPlans) {
    if (now - seenAt > IDEMPOTENT_PLAN_WINDOW_MS * 2) recentIdempotentPlans.delete(key)
  }
  const seenAt = recentIdempotentPlans.get(fingerprint)
  recentIdempotentPlans.set(fingerprint, now)
  if (seenAt === undefined || now - seenAt > IDEMPOTENT_PLAN_WINDOW_MS) return decision

  const presentation = steps.every((step) => String(step.name ?? '').startsWith('presentation_'))
  console.warn('[OfficePlan] duplicate-idempotent-plan-suppressed', {
    fingerprint,
    duplicateWindowMs: IDEMPOTENT_PLAN_WINDOW_MS,
  })
  return {
    kind: 'tool_call',
    toolCall: toolCall([{ name: presentation ? 'presentation_get_status' : 'system_get_status' }]),
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

  const resetPlanHistory = () => recentIdempotentPlans.clear()
  window.addEventListener('smartoffice:visit-activated', resetPlanHistory)
  window.addEventListener('smartoffice:visit-revoked', resetPlanHistory)

  realtimeOfficeInterpreter.interpret = async (text, language) => {
    const semanticDecision = decisionFromLatestSemanticRoute(text)
    if (semanticDecision) {
      const decision = suppressDuplicateIdempotentPlan(semanticDecision)
      console.info('[ConversationLatency] semantic-office-plan-reused', {
        textLength: text.length,
        decisionKind: decision.kind,
      })
      return decision
    }

    const deterministic = presentationFallback(text)
    if (deterministic) {
      const decision = suppressDuplicateIdempotentPlan(deterministic)
      console.info('[ConversationLatency] deterministic-presentation-fallback', {
        textLength: text.length,
        decisionKind: decision.kind,
      })
      return decision
    }

    const startedAt = performance.now()
    const originalDecision = await originalInterpret(text, language)
    const promoted = makeSlowPresentationNonBlocking(originalDecision)
    const decision = suppressDuplicateIdempotentPlan(promoted)
    console.info('[ConversationLatency] office-interpreter-fallback-complete', {
      elapsedMs: Math.round(performance.now() - startedAt),
      backgroundPresentationPromoted: promoted !== originalDecision,
      decisionKind: decision.kind,
    })
    return decision
  }
}
