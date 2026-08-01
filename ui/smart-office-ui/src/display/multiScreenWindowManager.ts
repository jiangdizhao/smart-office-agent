import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'

export type InteractionWindowKind = 'contact' | 'recording' | 'transcript' | 'results'

type DetailedScreen = {
  availLeft: number
  availTop: number
  availWidth: number
  availHeight: number
  left: number
  top: number
  width: number
  height: number
  label?: string
  isPrimary?: boolean
}

type ScreenDetailsLike = {
  screens: DetailedScreen[]
  currentScreen?: DetailedScreen
}

declare global {
  interface Window {
    getScreenDetails?: () => Promise<ScreenDetailsLike>
  }
}

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
  target: 'leftmost' | 'configured-fallback'
  message: string
}

const WINDOW_NAME = 'smart-office-left-touch-interaction'
let currentInteractionWindow: Window | null = null

function configuredNumber(name: string): number | null {
  const value = String(import.meta.env[name] ?? '').trim()
  if (!value) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function currentScreenFallback(): DetailedScreen {
  const screenWithPosition = window.screen as Screen &
    Partial<Pick<DetailedScreen, 'availLeft' | 'availTop'>>
  const width = configuredNumber('VITE_INTERACTION_WINDOW_WIDTH') ?? window.screen.availWidth
  const height = configuredNumber('VITE_INTERACTION_WINDOW_HEIGHT') ?? window.screen.availHeight
  const currentLeft = screenWithPosition.availLeft ?? window.screenX
  const currentTop = screenWithPosition.availTop ?? window.screenY
  const targetLeft = configuredNumber('VITE_INTERACTION_WINDOW_LEFT') ?? currentLeft - width
  const targetTop = configuredNumber('VITE_INTERACTION_WINDOW_TOP') ?? currentTop
  return {
    availLeft: targetLeft,
    availTop: targetTop,
    availWidth: width,
    availHeight: height,
    left: targetLeft,
    top: targetTop,
    width,
    height,
  }
}

async function resolveLeftmostScreen(): Promise<{
  screen: DetailedScreen
  target: InteractionWindowResult['target']
}> {
  if (typeof window.getScreenDetails === 'function') {
    try {
      const details = await window.getScreenDetails()
      const screens = [...details.screens].sort(
        (left, right) => left.left - right.left || left.top - right.top,
      )
      if (screens.length) {
        const requestedIndex = Math.max(
          0,
          Math.floor(configuredNumber('VITE_INTERACTION_SCREEN_INDEX') ?? 0),
        )
        return {
          screen: screens[Math.min(requestedIndex, screens.length - 1)],
          target: 'leftmost',
        }
      }
    } catch (error) {
      console.warn('[MultiScreen] getScreenDetails unavailable; using configured fallback', error)
    }
  }
  return { screen: currentScreenFallback(), target: 'configured-fallback' }
}

function interactionUrl(request: InteractionWindowRequest): string {
  const url = new URL(`/interaction/${request.kind}`, window.location.origin)
  url.searchParams.set('conversation_id', request.conversationId)
  if (request.visitId) url.searchParams.set('visit_id', request.visitId)
  url.searchParams.set('lang', request.language ?? 'zh')
  return url.toString()
}

function provisionalFeatures(): string {
  const width = configuredNumber('VITE_INTERACTION_WINDOW_WIDTH') ?? 1100
  const height = configuredNumber('VITE_INTERACTION_WINDOW_HEIGHT') ?? 800
  return [
    'popup=yes',
    'resizable=yes',
    'scrollbars=yes',
    `width=${Math.round(width)}`,
    `height=${Math.round(height)}`,
  ].join(',')
}

export async function openInteractionWindow(
  request: InteractionWindowRequest,
): Promise<InteractionWindowResult> {
  const reusable = currentInteractionWindow && !currentInteractionWindow.closed
  const popup = reusable
    ? currentInteractionWindow
    : window.open('about:blank', WINDOW_NAME, provisionalFeatures())

  if (!popup) {
    return {
      ok: false,
      blocked: true,
      reused: false,
      target: 'configured-fallback',
      message: 'The browser blocked the interaction window.',
    }
  }

  currentInteractionWindow = popup
  try {
    const { screen, target } = await resolveLeftmostScreen()
    const left = Math.round(screen.availLeft)
    const top = Math.round(screen.availTop)
    const width = Math.max(640, Math.round(screen.availWidth))
    const height = Math.max(540, Math.round(screen.availHeight))

    popup.location.replace(interactionUrl(request))
    popup.moveTo(left, top)
    popup.resizeTo(width, height)
    popup.focus()

    console.info('[MultiScreen] interaction-window-opened', {
      kind: request.kind,
      target,
      left,
      top,
      width,
      height,
      reused: Boolean(reusable),
    })
    return {
      ok: true,
      blocked: false,
      reused: Boolean(reusable),
      target,
      message: 'Interaction window opened on the left touch display.',
    }
  } catch (error) {
    console.error('[MultiScreen] interaction-window-placement-failed', error)
    try {
      popup.location.replace(interactionUrl(request))
      popup.focus()
    } catch {
      // The caller receives a clear failure result below.
    }
    return {
      ok: false,
      blocked: false,
      reused: Boolean(reusable),
      target: 'configured-fallback',
      message: error instanceof Error ? error.message : String(error),
    }
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
