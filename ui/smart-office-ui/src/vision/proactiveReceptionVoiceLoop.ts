import {
  openInteractionWindow,
  matchInteractionWindowIntent,
} from '../display/multiScreenWindowManager'
import { publishSessionMessage } from '../interaction/sessionEventBus'
import type { OfficeVoiceController } from '../voice/useOfficeVoiceController'
import { realtimeAgent } from '../voice/realtimeAgentRuntime'
import { voiceOutputManager } from '../voice/voiceOutputManager'
import { visitLeaseRegistry } from './visitLeaseRegistry'

export type AutomaticVoiceTurnResult =
  | { kind: 'heard'; transcript: string }
  | { kind: 'silence' }
  | { kind: 'aborted' }
  | { kind: 'error'; message: string }

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function interactionReply(kind: ReturnType<typeof matchInteractionWindowIntent>, language: 'zh' | 'en'): string {
  if (language === 'en') {
    if (kind === 'contact') return 'I have opened the contact registration form on the left touch display.'
    if (kind === 'recording') return 'I have opened live recording on the left touch display.'
    if (kind === 'transcript') return 'I have opened the current conversation transcript on the left touch display.'
    return 'I have opened the result center on the left touch display.'
  }
  if (kind === 'contact') return '我已经在左侧触摸屏打开登记信息表。'
  if (kind === 'recording') return '我已经在左侧触摸屏打开实时录音。'
  if (kind === 'transcript') return '我已经在左侧触摸屏打开当前对话记录。'
  return '我已经在左侧触摸屏打开结果中心。'
}

export async function captureAutomaticRealtimeTurn(
  controller: () => OfficeVoiceController,
  signal: AbortSignal,
): Promise<AutomaticVoiceTurnResult> {
  if (signal.aborted) return { kind: 'aborted' }
  try {
    const current = controller()
    await realtimeAgent.startContinuousCapture(current.language, signal)
    const transcript = await realtimeAgent.nextContinuousUtterance(signal)
    if (signal.aborted) return { kind: 'aborted' }

    const lease = visitLeaseRegistry.current()
    publishSessionMessage({
      conversationId: current.conversationId,
      visitId: lease?.visitId ?? null,
      role: 'user',
      text: transcript,
      source: 'continuous_realtime_vad',
    })
    window.dispatchEvent(new CustomEvent('smartoffice:continuous-user-transcript', {
      detail: { transcript },
    }))

    const interactionKind = matchInteractionWindowIntent(transcript)
    if (interactionKind) {
      const result = await openInteractionWindow({
        kind: interactionKind,
        conversationId: current.conversationId,
        visitId: lease?.visitId ?? null,
        language: current.language,
      })
      const reply = result.ok
        ? interactionReply(interactionKind, current.language)
        : current.language === 'zh'
          ? '浏览器阻止了窗口。请点击中间屏幕上的对应按钮，并允许本站弹出窗口。'
          : 'The browser blocked the window. Use the matching button on the middle display and allow pop-ups.'
      publishSessionMessage({
        conversationId: current.conversationId,
        visitId: lease?.visitId ?? null,
        role: 'assistant',
        text: reply,
        source: 'interaction_window_command',
      })
      window.dispatchEvent(new CustomEvent('smartoffice:direct-assistant-caption', {
        detail: { text: reply },
      }))
      await voiceOutputManager.speak(reply, current.language, {
        lease,
        signal,
        allowLocalFallback: false,
      })
      return { kind: 'heard', transcript }
    }

    await current.submit(transcript, 'voice')
    return { kind: 'heard', transcript }
  } catch (error) {
    if (signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
      await realtimeAgent.stopContinuousCapture(true).catch(() => undefined)
      return { kind: 'aborted' }
    }
    return { kind: 'error', message: errorText(error) }
  }
}
