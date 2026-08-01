import type { VisitLease } from '../vision/visitLeaseRegistry'
import {
  matchInteractionWindowIntent,
  openInteractionWindow,
  type InteractionWindowKind,
  type InteractionWindowResult,
} from '../display/multiScreenWindowManager'
import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const ROUTE_TIMEOUT_MS = 6_000
const SYSTEM_ACTION_TIMEOUT_MS = 22_000
const INTERACTION_CONTEXT_PREFIX = '__SMART_OFFICE_INTERACTION_WINDOW__:'
const SYSTEM_ACTION_CONTEXT_PREFIX = '__SMART_OFFICE_SYSTEM_ACTION__:'

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

type LegacyAgentResponse = {
  mode?: string
  user_request?: string
  results?: LegacyToolResult[]
}

type SystemActionContext = {
  kind: SystemActionKind
  result: LegacyToolResult
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
    if (signal?.aborted) throw abortError('Conversation route preview was aborted.')
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

function interactionReply(context: InteractionContext, language: VoiceLanguage): string {
  const labels: Record<InteractionWindowKind, { zh: string; en: string }> = {
    contact: { zh: '登记信息', en: 'contact registration' },
    recording: { zh: '实时录音', en: 'live recording' },
    transcript: { zh: '当前对话记录', en: 'the current conversation transcript' },
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

function normaliseSystemCommand(text: string): string {
  return text
    .toLocaleLowerCase()
    .replace(/\b(one\s*note)\b/gi, 'onenote')
    .replace(/\b(microsoft\s+teams)\b/gi, 'teams')
    .replace(/\s+/g, ' ')
    .trim()
}

function matchSystemAction(text: string): SystemActionKind | null {
  const clean = normaliseSystemCommand(text)
  if (!clean) return null

  const close =
    /(关闭|关掉|停止|退出|结束|别放了|不要播放|close|stop|quit|exit|turn off)/i.test(clean)
  const open =
    /(打开|启动|开启|播放|放一首|放点|来一首|open|launch|start|play|turn on)/i.test(clean)

  const music = /(音乐|歌曲|放歌|听歌|\bmusic\b|\bsong\b)/i.test(clean)
  if (music && close) return 'music_stop'
  if (music && open) return 'music_play_random'

  const teams = /(^|[^a-z])teams([^a-z]|$)|微软团队/i.test(clean)
  if (teams && close) return 'teams_close'
  if (teams && open) return 'teams_open'

  const onenote = /(^|[^a-z])onenote([^a-z]|$)|微软笔记/i.test(clean)
  if (onenote && close) return 'onenote_close'
  if (onenote && open) return 'onenote_open'

  return null
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

async function executeSystemAction(
  kind: SystemActionKind,
  request: RouteRequest,
): Promise<LegacyToolResult> {
  const response = await fetchWithTimeout(
    `${API_BASE_URL}/agent/run`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        text: request.text,
        execute: true,
      }),
    },
    SYSTEM_ACTION_TIMEOUT_MS,
    request.lease?.signal,
  )
  if (!response.ok) {
    throw new Error(
      `System action failed: ${response.status} ${await response.text()}`,
    )
  }
  const payload = (await response.json()) as LegacyAgentResponse
  const result = payload.results?.[0]
  if (!result) {
    return {
      tool_name: kind,
      ok: false,
      message: 'The Backend returned no tool result.',
      data: { verified: false },
    }
  }
  return result
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
    if (verified) {
      return language === 'zh'
        ? `好的，已经随机播放${trackName ? `《${trackName}》` : '一首本地音乐'}。`
        : `Okay. I randomly selected and started ${trackName || 'a local track'}.`
    }
    return language === 'zh'
      ? `已经把${trackName ? `《${trackName}》` : '随机选择的音乐'}交给默认媒体播放器，但暂时无法确认播放器窗口。`
      : `I sent ${trackName || 'the selected track'} to the default media player, but I could not positively identify the player window.`
  }

  if (kind === 'music_stop') {
    return language === 'zh'
      ? alreadyStopped
        ? '音乐当前已经停止。'
        : '音乐已经停止，受控媒体播放器也已关闭。'
      : alreadyStopped
        ? 'Music is already stopped.'
        : 'Music has stopped and the managed media player has been closed.'
  }

  const labels: Record<
    Exclude<SystemActionKind, 'music_play_random' | 'music_stop'>,
    { zh: string; en: string; action: 'open' | 'close' }
  > = {
    teams_open: { zh: 'Microsoft Teams', en: 'Microsoft Teams', action: 'open' },
    teams_close: { zh: 'Microsoft Teams', en: 'Microsoft Teams', action: 'close' },
    onenote_open: { zh: 'OneNote', en: 'OneNote', action: 'open' },
    onenote_close: { zh: 'OneNote', en: 'OneNote', action: 'close' },
  }
  const item = labels[kind]
  if (item.action === 'open') {
    return language === 'zh'
      ? alreadyRunning
        ? `${item.zh} 已经处于打开状态。`
        : `${item.zh} 已经打开并通过状态验证。`
      : alreadyRunning
        ? `${item.en} is already open.`
        : `${item.en} is open and its state was verified.`
  }
  return language === 'zh'
    ? alreadyStopped
      ? `${item.zh} 已经处于关闭状态。`
      : `${item.zh} 已经关闭并通过状态验证。`
    : alreadyStopped
      ? `${item.en} is already closed.`
      : `${item.en} is closed and its state was verified.`
}

export async function previewConversationRoute(
  request: RouteRequest,
): Promise<FastConversationRoute> {
  const interactionKind = matchInteractionWindowIntent(request.text)
  if (interactionKind) {
    const startedAt = performance.now()
    const result = await openInteractionWindow({
      kind: interactionKind,
      conversationId: request.conversationId,
      visitId: request.visitId,
      language: request.language,
    })
    console.info('[ConversationLatency] interaction-panel-command-complete', {
      kind: interactionKind,
      ok: result.ok,
      target: result.target,
      elapsedMs: Math.round(performance.now() - startedAt),
      visitId: request.visitId,
    })
    return {
      route: 'realtime_direct',
      scene: 'reception',
      route_reason: 'interaction_panel_command',
      conversation_complexity: 'simple',
      answer_engine: 'realtime',
      recent_context: interactionContext(interactionKind, result),
      visit_id: request.visitId,
    }
  }

  const systemAction = matchSystemAction(request.text)
  if (systemAction) {
    const startedAt = performance.now()
    const result = await executeSystemAction(systemAction, request)
    console.info('[ConversationLatency] deterministic-system-action-complete', {
      kind: systemAction,
      tool: result.tool_name ?? null,
      ok: result.ok === true,
      verified: result.data?.verified === true,
      elapsedMs: Math.round(performance.now() - startedAt),
      visitId: request.visitId,
    })
    return {
      route: 'realtime_direct',
      scene: 'office',
      route_reason: `deterministic_system_action:${systemAction}`,
      conversation_complexity: 'simple',
      answer_engine: 'realtime',
      recent_context: systemActionContext(systemAction, result),
      visit_id: request.visitId,
    }
  }

  const startedAt = performance.now()
  const response = await fetchWithTimeout(
    `${API_BASE_URL}/api/conversation-route`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: request.conversationId,
        visit_id: request.visitId,
        text: request.text,
        language: request.language,
        actor_type: request.actor,
      }),
    },
    ROUTE_TIMEOUT_MS,
    request.lease?.signal,
  )
  if (!response.ok) {
    throw new Error(
      `Conversation route preview failed: ${response.status} ${await response.text()}`,
    )
  }
  const payload = (await response.json()) as FastConversationRoute
  const elapsedMs = Math.round(performance.now() - startedAt)
  console.info('[OfficeRoute]', {
    transcript: request.text,
    route: payload.route,
    routeReason: payload.route_reason,
    answerEngine: payload.answer_engine,
    complexity: payload.conversation_complexity,
    elapsedMs,
    visitId: request.visitId,
  })
  console.info('[ConversationLatency] route-preview-complete', {
    route: payload.route,
    routeReason: payload.route_reason,
    complexity: payload.conversation_complexity,
    answerEngine: payload.answer_engine,
    elapsedMs,
    visitId: request.visitId,
  })
  return payload
}

function directInstructions(
  text: string,
  language: VoiceLanguage,
  recentContext: string,
): string {
  if (language === 'en') {
    return `
You are Sara, the friendly and professional Smart Office virtual host.
Answer the current visitor directly in natural spoken English.
This request has already been classified as a simple non-Office conversation, so do not call tools and do not claim that any Office action, email, file, presentation, or device change was executed.
Use the recent conversation only when relevant. When the previous assistant message invited the visitor to try a quick demonstration, treat a brief affirmative answer as acceptance and ask them to choose PowerPoint voice control, Outlook assistance, or a general question. Treat a clear refusal as a brief polite close without pressure.
Keep the answer concise: normally one to four short spoken sentences. State uncertainty rather than inventing facts.
Return only the final answer as plain text without labels, Markdown, or quotation marks.

Recent conversation:
${recentContext || '(none)'}

Current visitor message:
${text}
`.trim()
  }
  return `
你是 Sara，一位亲切、成熟、专业的 Smart Office 虚拟接待员。
请用自然口语中文直接回答当前访客。
该请求已经被确定性路由判定为简单的非 Office 对话，因此不要调用工具，也不得声称已经执行 Office 操作、发送邮件、创建文件、控制演示文稿或修改设备。
仅在相关时使用最近对话。如果上一条助手消息刚刚邀请访客体验快速演示，那么简短肯定回答表示接受，应请访客从 PowerPoint 语音控制、Outlook 助手或一般问题中选择；明确拒绝时应礼貌简短结束，不施压。
回答应简洁，通常一到四个适合朗读的短句。无法确定时说明不确定，不要编造。
只输出最终答复纯文本，不要输出标签、Markdown 或引号。

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
    visitId: lease?.visitId ?? null,
  })
  return answer
}
