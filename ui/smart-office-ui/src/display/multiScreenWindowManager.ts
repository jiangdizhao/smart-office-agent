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
    [
      '登记信息',
      '填写信息',
      '留下联系方式',
      '我想登记',
      '我要登记',
      '我想注册',
      '我想留下信息',
      'contact form',
      'registration form',
    ].includes(clean)
    || /(打开|显示|调出|进入|填写|登记|注册|留下|我想|我要|希望|帮我|请).{0,10}(登记信息|登记|注册|联系信息|联系方式|个人信息|访客信息|我的资料|我的信息)|(?:登记信息|联系信息|联系方式|个人信息).{0,8}(窗口|表单|页面)|\b(?:open|show|display|fill|register|sign up|leave)\b.{0,30}\b(?:contact form|contact details|my details|registration form)\b/.test(clean)
  ) return 'contact'

  if (
    [
      '实时录音',
      '开始录音',
      '现场录音',
      '我想录音',
      '帮我们录一下',
      'live recording',
    ].includes(clean)
    || /(打开|显示|调出|进入|开始|我想|我要|帮我|请|能不能|可以).{0,10}(实时录音|现场录音|对话录音|录音窗口|录音|录一下|录下来)|(?:实时录音|现场录音|对话录音).{0,8}(窗口|页面)|\b(?:open|show|start|record)\b.{0,24}\b(?:live recording|recording window|conversation recording|our conversation)\b/.test(clean)
  ) return 'recording'

  if (
    [
      '对话记录',
      '聊天记录',
      '会话记录',
      '刚才我们说了什么',
      'conversation history',
      'chat history',
    ].includes(clean)
    || /(打开|显示|调出|查看|看看|我想|我要|让我|请).{0,10}(对话记录|聊天记录|会话记录|当前对话|刚才的对话|刚才我们说了什么)|(?:对话记录|聊天记录|会话记录).{0,8}(窗口|页面)|\b(?:open|show|display|view|see)\b.{0,24}\b(?:conversation history|chat history|conversation transcript|current transcript|what we said)\b/.test(clean)
  ) return 'transcript'

  if (
    [
      '结果中心',
      '查看结果',
      '已登记信息',
      '录音列表',
      '查看保存的资料',
      'result center',
    ].includes(clean)
    || /(打开|显示|调出|进入|查看|看看|我想|我要|让我|请).{0,10}(结果中心|登记结果|已登记信息|联系人列表|录音列表|保存结果|保存的资料|客户资料|收集的结果)|(?:录音|登记资料|客户资料).{0,8}(保存在哪|在哪里|列表|结果)|\b(?:open|show|view|see)\b.{0,24}\b(?:result center|saved results|contact list|recording list|registered visitors)\b/.test(clean)
  ) return 'results'

  return null
}
