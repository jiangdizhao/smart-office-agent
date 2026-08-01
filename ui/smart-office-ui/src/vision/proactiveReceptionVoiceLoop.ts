import {
  openInteractionWindow,
  matchInteractionWindowIntent,
} from '../display/multiScreenWindowManager'
import { publishSessionMessage } from '../interaction/sessionEventBus'
import {
  commandClarification,
  recoverCommandTranscript,
} from '../voice/commandSpeechRecovery'
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
    if (kind === 'contact') return 'I have opened contact registration beside Sara on the main display.'
    if (kind === 'recording') return 'I have opened live recording beside Sara on the main display.'
    if (kind === 'transcript') return 'I have opened the current conversation transcript beside Sara on the main display.'
    return 'I have opened the result center beside Sara on the main display.'
  }
  if (kind === 'contact') return '我已经在主屏幕 Sara 左侧打开登记信息表。'
  if (kind === 'recording') return '我已经在主屏幕 Sara 左侧打开实时录音。'
  if (kind === 'transcript') return '我已经在主屏幕 Sara 左侧打开当前对话记录。'
  return '我已经在主屏幕 Sara 左侧打开结果中心。'
}

async function speakDirect(
  controller: OfficeVoiceController,
  text: string,
  source: string,
  signal: AbortSignal,
): Promise<void> {
  const lease = visitLeaseRegistry.current()
  publishSessionMessage({
    conversationId: controller.conversationId,
    visitId: lease?.visitId ?? null,
    role: 'assistant',
    text,
    source,
  })
  window.dispatchEvent(new CustomEvent('smartoffice:direct-assistant-caption', {
    detail: { text },
  }))
  await voiceOutputManager.speak(text, controller.language, {
    lease,
    signal,
    allowLocalFallback: false,
  })
}

async function recoverTurnState(
  controller: () => OfficeVoiceController,
  signal: AbortSignal,
): Promise<boolean> {
  // React state propagation is asynchronous. Yield once, then inspect the latest
  // controller snapshot rather than the snapshot captured before submit().
  await new Promise((resolve) => window.setTimeout(resolve, 0))
  if (signal.aborted) return false
  const latest = controller()
  if (latest.panel !== 'error') return true

  console.warn('[RealtimeDiagnostics] recovering-controller-after-turn-error', {
    error: latest.error,
    conversationId: latest.conversationId,
  })
  latest.clearError()
  await latest.connect()
  await new Promise((resolve) => window.setTimeout(resolve, 0))
  const recovered = !signal.aborted && controller().panel !== 'error'
  console.info('[RealtimeDiagnostics] controller-turn-recovery-complete', {
    recovered,
    panel: controller().panel,
    connectionState: realtimeAgent.status().connectionState,
  })
  return recovered
}

export async function captureAutomaticRealtimeTurn(
  controller: () => OfficeVoiceController,
  signal: AbortSignal,
): Promise<AutomaticVoiceTurnResult> {
  if (signal.aborted) return { kind: 'aborted' }
  try {
    const current = controller()
    await realtimeAgent.startContinuousCapture(current.language, signal)
    const rawTranscript = await realtimeAgent.nextContinuousUtterance(signal)
    if (signal.aborted) return { kind: 'aborted' }

    const recovered = recoverCommandTranscript(rawTranscript, current.language)
    const transcript = recovered.normalized
    const lease = visitLeaseRegistry.current()
    publishSessionMessage({
      conversationId: current.conversationId,
      visitId: lease?.visitId ?? null,
      role: 'user',
      text: transcript,
      source: recovered.recovered
        ? 'continuous_realtime_vad_command_recovery'
        : 'continuous_realtime_vad',
    })
    window.dispatchEvent(new CustomEvent('smartoffice:continuous-user-transcript', {
      detail: {
        transcript,
        rawTranscript: recovered.raw,
        commandRecovered: recovered.recovered,
      },
    }))

    const clarification = commandClarification(recovered)
    if (clarification) {
      console.info('[RealtimeDiagnostics] bounded-command-clarification', {
        rawTranscript: recovered.raw,
        normalizedTranscript: transcript,
        target: recovered.target,
        language: current.language,
      })
      await speakDirect(current, clarification, 'bounded_command_clarification', signal)
      return { kind: 'heard', transcript }
    }

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
          ? '主屏幕交互面板没有成功打开，请刷新页面后再试。'
          : 'The main-display interaction panel did not open. Please refresh the page and try again.'
      await speakDirect(current, reply, 'interaction_window_command', signal)
      return { kind: 'heard', transcript }
    }

    await current.submit(transcript, 'voice')
    const healthy = await recoverTurnState(controller, signal)
    if (!healthy) {
      return {
        kind: 'error',
        message: 'The voice turn failed and the controller could not recover automatically.',
      }
    }
    return { kind: 'heard', transcript }
  } catch (error) {
    if (signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
      await realtimeAgent.stopContinuousCapture(true).catch(() => undefined)
      return { kind: 'aborted' }
    }

    const recovered = await recoverTurnState(controller, signal).catch(() => false)
    return {
      kind: 'error',
      message: recovered
        ? `${errorText(error)} The controller recovered and listening will continue.`
        : errorText(error),
    }
  }
}
