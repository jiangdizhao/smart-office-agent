import {
  currentInteractionPanel,
  openInteractionWindow,
  type InteractionWindowKind,
  type InteractionWindowResult,
} from '../display/multiScreenWindowManager'
import {
  requestUnifiedSemanticRoute,
  type UnifiedSemanticRoute,
  type UnifiedSemanticRouteResponse,
} from '../routing/unifiedSemanticRouterClient'
import type { VisitLease } from '../vision/visitLeaseRegistry'
import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const SYSTEM_ACTION_TIMEOUT_MS = 22_000
const INTERACTION_CONTEXT_PREFIX = '__SMART_OFFICE_INTERACTION_WINDOW__:'
const SYSTEM_ACTION_CONTEXT_PREFIX = '__SMART_OFFICE_SYSTEM_ACTION__:'
const SEMANTIC_ANSWER_CONTEXT_PREFIX = '__SMART_OFFICE_SEMANTIC_ANSWER__:'

export type FastConversationAnswerEngine =
  | 'realtime'
  | 'terra'
  | 'office_interpreter'
  | 'backend'

export type FastConversationRoute = {
  route: string
  scene: string
  route_reason: string
  conversation_complexity: 'simple' | 'complex' | 'not_applicable'
  answer_engine: FastConversationAnswerEngine
  recent_context: string
  visit_id?: string | null
  semantic_decision?: UnifiedSemanticRouteResponse | null
}

type RouteRequest = {
  conversationId: string
  visitId: string | null
  text: string
  language: VoiceLanguage
  actor: 'visitor' | 'employee' | 'operator'
  lease: VisitLease | null
}

type InteractionContext = {
  kind: InteractionWindowKind
  result: InteractionWindowResult
}

type SystemActionKind =
  | 'music_play_random'
  | 'music_stop'
  | 'teams_open'
  | 'teams_close'
  | 'onenote_open'
  | 'onenote_close'

type LegacyToolResult = {
  tool_name?: string
  ok?: boolean
  message?: string
  artifacts?: string[]
  data?: Record<string, unknown>
}

type LegacyTaskStep = {
  status?: string
  message?: string | null
  result?: LegacyToolResult | null
}

type LegacyTaskSession = {
  task_id?: string
  status?: string
  summary?: string | null
  steps?: LegacyTaskStep[]
}

type SystemActionContext = {
  kind: SystemActionKind
  result: LegacyToolResult
}

type SemanticAnswerContext = {
  text: string
  purpose: 'clarification' | 'confirmation' | 'rejection'
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<Response> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort, { once: true })
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } catch (error) {
    if (signal?.aborted) throw abortError('Conversation route belongs to a stale Visit.')
    throw error
  } finally {
    window.clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}

function interactionContext(kind: InteractionWindowKind, result: InteractionWindowResult): string {
  return `${INTERACTION_CONTEXT_PREFIX}${JSON.stringify({ kind, result } satisfies InteractionContext)}`
}

function parseInteractionContext(value: string): InteractionContext | null {
  if (!value.startsWith(INTERACTION_CONTEXT_PREFIX)) return null
  try {
    const parsed = JSON.parse(value.slice(INTERACTION_CONTEXT_PREFIX.length)) as InteractionContext
    if (!parsed?.kind || !parsed?.result) return null
    return parsed
  } catch {
    return null
  }
}

function semanticAnswerContext(
  text: string,
  purpose: SemanticAnswerContext['purpose'],
): string {
  return `${SEMANTIC_ANSWER_CONTEXT_PREFIX}${JSON.stringify({ text, purpose } satisfies SemanticAnswerContext)}`
}

function parseSemanticAnswerContext(value: string): SemanticAnswerContext | null {
  if (!value.startsWith(SEMANTIC_ANSWER_CONTEXT_PREFIX)) return null
  try {
    const parsed = JSON.parse(
      value.slice(SEMANTIC_ANSWER_CONTEXT_PREFIX.length),
    ) as SemanticAnswerContext
    return parsed?.text?.trim() ? parsed : null
  } catch {
    return null
  }
}

function semanticRecentContext(route: UnifiedSemanticRoute): string {
  const value = route.entities?.recent_context
  return typeof value === 'string' ? value.trim() : ''
}

function interactionReply(context: InteractionContext, language: VoiceLanguage): string {
  const labels: Record<InteractionWindowKind, { zh: string; en: string }> = {
    contact: { zh: '登记信息', en: 'contact registration' },
    meeting: { zh: '会议预约日历', en: 'the meeting-booking calendar' },
    recording: { zh: '实时录音', en: 'live recording' },
    transcript: { zh: '本 Session 对话总结', en: 'the current Session summary' },
    results: { zh: '结果中心', en: 'the result center' },
  }
  const label = labels[context.kind][language]
  if (context.result.ok) {
    return language === 'zh'
      ? `好的，我已经在主屏幕 Sara 左侧打开${label}。您可以保持在摄像头前完成操作。`
      : `Okay. I opened ${label} beside Sara on the main display, so you can complete it while remaining in view of the camera.`
  }
  return language === 'zh'
    ? `${label}面板没有成功打开。请刷新主屏幕后再试一次。`
    : `The ${label} panel did not open. Please refresh the main display and try again.`
}

function systemActionContext(kind: SystemActionKind, result: LegacyToolResult): string {
  return `${SYSTEM_ACTION_CONTEXT_PREFIX}${JSON.stringify({ kind, result } satisfies SystemActionContext)}`
}

function parseSystemActionContext(value: string): SystemActionContext | null {
  if (!value.startsWith(SYSTEM_ACTION_CONTEXT_PREFIX)) return null
  try {
    const parsed = JSON.parse(
      value.slice(SYSTEM_ACTION_CONTEXT_PREFIX.length),
    ) as SystemActionContext
    if (!parsed?.kind || !parsed?.result) return null
    return parsed
  } catch {
    return null
  }
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortError('System action was aborted.'))
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup()
      resolve()
    }, milliseconds)
    const onAbort = () => {
      window.clearTimeout(timer)
      cleanup()
      reject(abortError('System action was aborted.'))
    }
    const cleanup = () => signal?.removeEventListener('abort', onAbort)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

async function cancelSystemActionTask(taskId: string): Promise<void> {
  if (!taskId) return
  await fetch(`${API_BASE_URL}/agent/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
    keepalive: true,
  }).catch(() => undefined)
}

function latestTaskResult(task: LegacyTaskSession): LegacyToolResult | null {
  const steps = [...(task.steps ?? [])].reverse()
  return steps.find((step) => step.result)?.result ?? null
}

function canonicalSystemCommand(kind: SystemActionKind): string {
  const commands: Record<SystemActionKind, string> = {
    music_play_random: '播放音乐',
    music_stop: '停止音乐',
    teams_open: '打开 Teams',
    teams_close: '关闭 Teams',
    onenote_open: '打开 OneNote',
    onenote_close: '关闭 OneNote',
  }
  return commands[kind]
}

async function executeSystemAction(
  kind: SystemActionKind,
  request: RouteRequest,
): Promise<LegacyToolResult> {
  let taskId = ''
  try {
    const createResponse = await fetchWithTimeout(
      `${API_BASE_URL}/agent/tasks`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          // The legacy planner now receives only an allowlisted canonical command.
          // It no longer interprets arbitrary visitor language on the unified path.
          text: canonicalSystemCommand(kind),
          execute: true,
          conversation_id: request.conversationId,
          visit_id: request.visitId,
          actor_type: request.actor,
        }),
      },
      6_000,
      request.lease?.signal,
    )
    if (!createResponse.ok) {
      throw new Error(
        `System action task creation failed: ${createResponse.status} ${await createResponse.text()}`,
      )
    }

    let task = (await createResponse.json()) as LegacyTaskSession
    taskId = String(task.task_id ?? '').trim()
    if (!taskId) throw new Error('The Backend returned no system action task id.')

    const deadline = performance.now() + SYSTEM_ACTION_TIMEOUT_MS
    while (performance.now() < deadline) {
      const status = String(task.status ?? '')
      if (['completed', 'failed', 'cancelled'].includes(status)) {
        const result = latestTaskResult(task)
        if (result) return result
        return {
          tool_name: kind,
          ok: false,
          message:
            task.summary?.trim()
            || `System action ended with status ${status || 'unknown'} without a result.`,
          data: { verified: false, task_id: taskId, task_status: status },
        }
      }
      await wait(250, request.lease?.signal)
      const pollResponse = await fetchWithTimeout(
        `${API_BASE_URL}/agent/tasks/${encodeURIComponent(taskId)}`,
        { headers: { Accept: 'application/json' } },
        4_000,
        request.lease?.signal,
      )
      if (!pollResponse.ok) {
        throw new Error(
          `System action status failed: ${pollResponse.status} ${await pollResponse.text()}`,
        )
      }
      task = (await pollResponse.json()) as LegacyTaskSession
    }
    throw new Error('The system action did not finish within the configured timeout.')
  } catch (error) {
    if (taskId) void cancelSystemActionTask(taskId)
    throw error
  }
}

function booleanValue(value: unknown): boolean {
  return value === true
}

function systemActionReply(context: SystemActionContext, language: VoiceLanguage): string {
  const { kind, result } = context
  const data = result.data ?? {}
  const ok = result.ok === true
  const verified = booleanValue(data.verified)
  const alreadyRunning = booleanValue(data.already_running)
  const alreadyStopped = booleanValue(data.already_stopped)
  const trackName = String(data.selected_track_name ?? '').trim()

  if (!ok) {
    const detail = String(result.message ?? '').trim()
    return language === 'zh'
      ? `没有完成这项操作。${detail || '请检查应用安装和本机配置。'}`
      : `I could not complete that action. ${detail || 'Please check the application installation and local configuration.'}`
  }
  if (kind === 'music_play_random') {
    return verified
      ? language === 'zh'
        ? `好的，已经随机播放${trackName ? `《${trackName}》` : '一首本地音乐'}。`
        : `Okay. I randomly selected and started ${trackName || 'a local track'}.`
      : language === 'zh'
        ? `已经把${trackName ? `《${trackName}》` : '随机选择的音乐'}交给默认媒体播放器，但暂时无法确认播放器窗口。`
        : `I sent ${trackName || 'the selected track'} to the media player, but could not verify its window.`
  }
  if (kind === 'music_stop') {
    return language === 'zh'
      ? alreadyStopped ? '音乐当前已经停止。' : '音乐已经停止，受控媒体播放器也已关闭。'
      : alreadyStopped ? 'Music is already stopped.' : 'Music has stopped and the managed player has closed.'
  }
  const labels: Record<Exclude<SystemActionKind, 'music_play_random' | 'music_stop'>, {
    zh: string
    en: string
    action: 'open' | 'close'
  }> = {
    teams_open: { zh: 'Microsoft Teams', en: 'Microsoft Teams', action: 'open' },
    teams_close: { zh: 'Microsoft Teams', en: 'Microsoft Teams', action: 'close' },
    onenote_open: { zh: 'OneNote', en: 'OneNote', action: 'open' },
    onenote_close: { zh: 'OneNote', en: 'OneNote', action: 'close' },
  }
  const item = labels[kind]
  if (item.action === 'open') {
    return language === 'zh'
      ? alreadyRunning ? `${item.zh} 已经处于打开状态。` : `${item.zh} 已经打开并通过状态验证。`
      : alreadyRunning ? `${item.en} is already open.` : `${item.en} is open and verified.`
  }
  return language === 'zh'
    ? alreadyStopped ? `${item.zh} 已经处于关闭状态。` : `${item.zh} 已经关闭并通过状态验证。`
    : alreadyStopped ? `${item.en} is already closed.` : `${item.en} is closed and verified.`
}

function interactionKind(route: UnifiedSemanticRoute): InteractionWindowKind | null {
  const target = route.actions[0]?.target
  const mapping: Record<string, InteractionWindowKind> = {
    contact_registration: 'contact',
    meeting_booking: 'meeting',
    recording: 'recording',
    transcript: 'transcript',
    result_center: 'results',
  }
  return mapping[target] ?? null
}

function systemActionKind(route: UnifiedSemanticRoute): SystemActionKind | null {
  const action = route.actions[0]
  if (!action) return null
  const key = `${action.target}:${action.verb}`
  const mapping: Record<string, SystemActionKind> = {
    'music:start': 'music_play_random',
    'music:open': 'music_play_random',
    'music:stop': 'music_stop',
    'music:close': 'music_stop',
    'teams:open': 'teams_open',
    'teams:close': 'teams_close',
    'onenote:open': 'onenote_open',
    'onenote:close': 'onenote_close',
  }
  return mapping[key] ?? null
}

function clarificationRoute(
  semantic: UnifiedSemanticRouteResponse,
  request: RouteRequest,
): FastConversationRoute {
  const text = semantic.route.clarification_question?.trim()
    || (request.language === 'zh'
      ? '请明确告诉我是要执行操作，还是只想了解这个功能。'
      : 'Please clarify whether you want the action performed or only an explanation.')
  return {
    route: 'realtime_direct',
    scene: 'reception',
    route_reason: `semantic_clarification:${semantic.route.primary_intent}`,
    conversation_complexity: 'simple',
    answer_engine: 'realtime',
    recent_context: semanticAnswerContext(text, 'clarification'),
    visit_id: request.visitId,
    semantic_decision: semantic,
  }
}

export async function previewConversationRoute(
  request: RouteRequest,
): Promise<FastConversationRoute> {
  const startedAt = performance.now()
  const semantic = await requestUnifiedSemanticRoute({
    ...request,
    interactionPanel: currentInteractionPanel()?.kind ?? null,
    activeTool: document.body.classList.contains('smartoffice-tool-active') ? 'active' : null,
  })
  const route = semantic.route
  const finalDecision = semantic.final_policy_decision
  const routingArchitecture = String(route.entities?.routing_architecture ?? 'unknown')

  console.info('[SemanticRoute]', {
    decisionId: semantic.decision_id,
    mode: semantic.mode,
    routingArchitecture,
    source: route.source,
    intent: route.primary_intent,
    domain: route.domain,
    actionMode: route.action_mode,
    finalDecision,
    risk: route.risk,
    confidence: route.confidence,
    actions: route.actions,
    negatedActions: route.negated_actions,
    reasonCodes: route.reason_codes,
    policyReasons: semantic.policy_reason_codes,
    elapsedMs: semantic.elapsed_ms,
    visitId: request.visitId,
  })

  if (finalDecision === 'clarify' || route.requires_clarification) {
    return clarificationRoute(semantic, request)
  }
  if (finalDecision === 'request_confirmation') {
    const text = request.language === 'zh'
      ? '这项操作会产生外部影响。请明确确认是否继续。'
      : 'This action has an external effect. Please explicitly confirm whether to continue.'
    return {
      ...clarificationRoute(semantic, request),
      route_reason: `semantic_confirmation_required:${route.primary_intent}`,
      recent_context: semanticAnswerContext(text, 'confirmation'),
    }
  }
  if (finalDecision === 'reject') {
    const text = request.language === 'zh'
      ? '这项操作不能在当前状态下执行。'
      : 'That action cannot be performed in the current state.'
    return {
      ...clarificationRoute(semantic, request),
      route_reason: `semantic_policy_rejection:${route.primary_intent}`,
      recent_context: semanticAnswerContext(text, 'rejection'),
    }
  }

  if (route.domain === 'interaction' && finalDecision === 'execute') {
    const kind = interactionKind(route)
    if (!kind) return clarificationRoute(semantic, request)
    const result = await openInteractionWindow({
      kind,
      conversationId: request.conversationId,
      visitId: request.visitId,
      language: request.language,
    })
    return {
      route: 'realtime_direct',
      scene: 'reception',
      route_reason: `semantic_interaction_action:${kind}`,
      conversation_complexity: 'simple',
      answer_engine: 'realtime',
      recent_context: interactionContext(kind, result),
      visit_id: request.visitId,
      semantic_decision: semantic,
    }
  }

  if (route.domain === 'office' && finalDecision === 'execute') {
    const actionKind = systemActionKind(route)
    if (actionKind) {
      const result = await executeSystemAction(actionKind, request)
      return {
        route: 'realtime_direct',
        scene: 'office',
        route_reason: `semantic_verified_system_action:${actionKind}`,
        conversation_complexity: 'simple',
        answer_engine: 'realtime',
        recent_context: systemActionContext(actionKind, result),
        visit_id: request.visitId,
        semantic_decision: semantic,
      }
    }
    // Presentation, volume, email and compound Office work are delegated to the
    // existing domain interpreter only after the unified route and policy gates.
    return {
      route: 'office_direct',
      scene: 'office',
      route_reason: `semantic_office_action:${route.primary_intent}`,
      conversation_complexity: 'not_applicable',
      answer_engine: 'office_interpreter',
      recent_context: '',
      visit_id: request.visitId,
      semantic_decision: semantic,
    }
  }

  if (route.domain === 'office' && finalDecision === 'delegate') {
    return {
      route: 'office_direct',
      scene: 'office',
      route_reason: `semantic_office_delegate:${route.primary_intent}`,
      conversation_complexity: 'not_applicable',
      answer_engine: 'office_interpreter',
      recent_context: '',
      visit_id: request.visitId,
      semantic_decision: semantic,
    }
  }

  const answerEngine = route.answer_engine
  const realtimeRoute = answerEngine === 'terra' ? 'general_chat' : 'realtime_direct'
  const recentContext = semanticRecentContext(route)
  console.info('[ConversationLatency] semantic-route-complete', {
    elapsedMs: Math.round(performance.now() - startedAt),
    semanticElapsedMs: semantic.elapsed_ms,
    routingArchitecture,
    domain: route.domain,
    intent: route.primary_intent,
    answerEngine,
    recentContextCharacters: recentContext.length,
    visitId: request.visitId,
  })
  return {
    route: realtimeRoute,
    scene: route.domain === 'office' ? 'office' : 'reception',
    route_reason: `semantic_${route.domain}:${route.primary_intent}`,
    conversation_complexity: route.complexity,
    answer_engine: answerEngine,
    recent_context: recentContext,
    visit_id: request.visitId,
    semantic_decision: semantic,
  }
}

function directInstructions(
  text: string,
  language: VoiceLanguage,
  recentContext: string,
): string {
  if (language === 'en') {
    return `
You are Sara, the Smart Office Digital Manager and Enterprise Solution Consultant.
Answer the current visitor directly in natural spoken English. The routing layer classified this as an answer-only conversation, not an Office execution request. Do not call tools and do not claim an Office action was executed.
Use the recent conversation when it is relevant, especially for references such as this industry, that option, the second one, or what we just discussed. Distinguish explaining a capability from performing it. Respect every negation and condition in the visitor's wording. Keep the answer concise unless detailed analysis was requested. State uncertainty rather than inventing facts.
Return only plain final text without labels or Markdown.

Recent conversation:
${recentContext || '(none)'}

Current visitor message:
${text}
`.trim()
  }
  return `
你是 Sara，公司的 Smart Office 数字管理员与企业解决方案顾问。
路由层已经把当前请求判定为只需回答的自然对话，而不是 Office 执行指令。请使用自然口语中文直接回答，不调用工具，也不得声称已经执行 Office 操作。
在相关时必须使用最近对话，尤其要理解“这个行业”“刚才那个”“第二种”“那你觉得呢”等承接表达。必须区分“介绍或讨论功能”和“要求执行功能”，并严格尊重用户表达中的否定、条件和假设。除非用户要求详细分析，否则保持简洁；无法确定时明确说明，不得编造。
只输出最终答复纯文本，不要输出标签或 Markdown。

最近对话：
${recentContext || '（无）'}

当前访客的话：
${text}
`.trim()
}

export async function generateSimpleRealtimeAnswer(
  text: string,
  language: VoiceLanguage,
  recentContext: string,
  lease: VisitLease | null,
): Promise<string> {
  const semanticAnswer = parseSemanticAnswerContext(recentContext)
  if (semanticAnswer) return semanticAnswer.text.trim()

  const interaction = parseInteractionContext(recentContext)
  if (interaction) return interactionReply(interaction, language)

  const systemAction = parseSystemActionContext(recentContext)
  if (systemAction) return systemActionReply(systemAction, language)

  const startedAt = performance.now()
  const answer = (
    await realtimeAgent.generateText(
      directInstructions(text, language, recentContext),
      language,
      'simple_conversation_answer',
      lease?.signal,
    )
  ).trim()
  if (!answer) throw new Error('GPT Realtime returned no simple-conversation answer.')
  console.info('[ConversationLatency] realtime-simple-answer-complete', {
    elapsedMs: Math.round(performance.now() - startedAt),
    answerLength: answer.length,
    recentContextCharacters: recentContext.length,
    visitId: lease?.visitId ?? null,
  })
  return answer
}
