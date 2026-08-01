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
const INTERACTION_CONTEXT_PREFIX = '__SMART_OFFICE_INTERACTION_WINDOW__:'

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
