import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'

export type InteractionWindowKind = 'contact' | 'recording' | 'transcript' | 'results'

export const INTERACTION_PANEL_OPEN_EVENT = 'smartoffice:interaction-panel-open'
export const INTERACTION_PANEL_CLOSE_MESSAGE = 'smartoffice:interaction-panel-close'

export type InteractionWindowRequest = {
  kind: InteractionWindowKind
  conversationId: string
  visitId?: string | null
  language?: VoiceLanguage
}

export type InteractionWindowResult = {
  ok: boolean
  blocked: boolean
  reused: boolean
  target: 'host-overlay'
  message: string
}

export function interactionPanelUrl(request: InteractionWindowRequest): string {
  const url = new URL(`/interaction/${request.kind}`, window.location.origin)
  url.searchParams.set('conversation_id', request.conversationId)
  if (request.visitId) url.searchParams.set('visit_id', request.visitId)
  url.searchParams.set('lang', request.language ?? 'zh')
  url.searchParams.set('embedded', '1')
  return url.toString()
}

export async function openInteractionWindow(
  request: InteractionWindowRequest,
): Promise<InteractionWindowResult> {
  window.dispatchEvent(
    new CustomEvent<InteractionWindowRequest>(INTERACTION_PANEL_OPEN_EVENT, {
      detail: request,
    }),
  )
  console.info('[InteractionPanel] opened-on-host-display', {
    kind: request.kind,
    visitId: request.visitId ?? null,
    conversationId: request.conversationId,
  })
  return {
    ok: true,
    blocked: false,
    reused: true,
    target: 'host-overlay',
    message: 'Interaction panel opened beside Sara on the host display.',
  }
}

export function matchInteractionWindowIntent(text: string): InteractionWindowKind | null {
  const clean = text.trim().toLocaleLowerCase()
  if (!clean) return null

  if (
    ['登记信息', '填写信息', '留下联系方式', 'contact form', 'registration form'].includes(clean) ||
    /(打开|显示|调出|进入|填写|登记|留下).{0,8}(登记信息|联系信息|联系方式|个人信息)|(?:登记信息|联系信息|联系方式|个人信息).{0,8}(窗口|表单|页面)|\b(?:open|show|display|fill|register|leave)\b.{0,30}\b(?:contact form|contact details|my details|registration form)\b/.test(clean)
  ) return 'contact'

  if (
    ['实时录音', '开始录音', '现场录音', 'live recording'].includes(clean) ||
    /(打开|显示|调出|进入|开始).{0,8}(实时录音|现场录音|对话录音|录音窗口)|(?:实时录音|现场录音|对话录音).{0,8}(窗口|页面)|\b(?:open|show|start)\b.{0,24}\b(?:live recording|recording window|conversation recording)\b/.test(clean)
  ) return 'recording'

  if (
    ['对话记录', '聊天记录', '会话记录', 'conversation history', 'chat history'].includes(clean) ||
    /(打开|显示|调出|查看|看看).{0,8}(对话记录|聊天记录|会话记录|当前对话)|(?:对话记录|聊天记录|会话记录).{0,8}(窗口|页面)|\b(?:open|show|display|view)\b.{0,24}\b(?:conversation history|chat history|conversation transcript|current transcript)\b/.test(clean)
  ) return 'transcript'

  if (
    ['结果中心', '查看结果', '已登记信息', '录音列表', 'result center'].includes(clean) ||
    /(打开|显示|调出|进入|查看).{0,8}(结果中心|登记结果|已登记信息|联系人列表|录音列表|保存结果)|\b(?:open|show|view)\b.{0,24}\b(?:result center|saved results|contact list|recording list)\b/.test(clean)
  ) return 'results'

  return null
}
