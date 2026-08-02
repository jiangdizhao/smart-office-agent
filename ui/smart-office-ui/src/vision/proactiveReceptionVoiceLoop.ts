import {
  openInteractionWindow,
  matchInteractionWindowIntent,
  type InteractionWindowKind,
} from '../display/multiScreenWindowManager'
import {
  resolveSemanticInteractionIntent,
} from '../interaction/semanticInteractionInterpreter'
import { publishSessionMessage } from '../interaction/sessionEventBus'
import {
  commandClarification,
  recoverCommandTranscript,
  type CommandAction,
  type CommandTarget,
} from '../voice/commandSpeechRecovery'
import {
  OFFICE_API_BASE,
  type OfficeVoiceController,
} from '../voice/useOfficeVoiceController'
import { realtimeAgent } from '../voice/realtimeAgentRuntime'
import { voiceOutputManager } from '../voice/voiceOutputManager'
import { visitLeaseRegistry } from './visitLeaseRegistry'

export type AutomaticVoiceTurnResult =
  | { kind: 'heard'; transcript: string }
  | { kind: 'silence' }
  | { kind: 'aborted' }
  | { kind: 'error'; message: string }

type DesktopToolResult = {
  tool_name?: string
  ok?: boolean
  message?: string
  data?: Record<string, unknown>
}

type DesktopAction =
  | 'open_powerpoint'
  | 'close_powerpoint'
  | 'open_teams'
  | 'close_teams'
  | 'open_onenote'
  | 'close_onenote'
  | 'play_music'
  | 'stop_music'

type DesktopCommandResponse = {
  action?: DesktopAction
  tool_name?: string
  result?: DesktopToolResult
}

type PendingInteractionConfirmation = {
  kind: InteractionWindowKind
  visitId: string | null
  expiresAt: number
}

let pendingInteractionConfirmation: PendingInteractionConfirmation | null = null

const CONFIRM_PATTERN = /^(?:是|是的|对|对的|好的|好|可以|确认|没错|yes|yeah|yep|correct|please do|go ahead)$/i
const REJECT_PATTERN = /^(?:不|不是|不要|取消|算了|不用|no|nope|cancel|never mind)$/i

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function waitWithSignal(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) {
    return Promise.reject(new DOMException('Operation aborted.', 'AbortError'))
  }
  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      signal.removeEventListener('abort', onAbort)
      if (error) reject(error)
      else resolve()
    }
    const onAbort = () => finish(new DOMException('Operation aborted.', 'AbortError'))
    const timer = window.setTimeout(() => finish(), milliseconds)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function interactionReply(
  kind: InteractionWindowKind,
  language: 'zh' | 'en',
): string {
  if (language === 'en') {
    if (kind === 'contact') return 'I have opened contact registration beside Sara on the main display.'
    if (kind === 'recording') return 'I have opened live recording beside Sara on the main display.'
    if (kind === 'transcript') return 'I have opened the current conversation transcript beside Sara on the main display.'
    return 'I have opened the administrator verification screen. Enter the administrator password on the display to access the result center.'
  }
  if (kind === 'contact') return '我已经在主屏幕 Sara 左侧打开登记信息表。'
  if (kind === 'recording') return '我已经在主屏幕 Sara 左侧打开实时录音。'
  if (kind === 'transcript') return '我已经在主屏幕 Sara 左侧打开当前对话记录。'
  return '我已经打开管理员验证界面，请在屏幕上输入管理员密码后进入结果中心。'
}

function interactionConfirmation(
  kind: InteractionWindowKind,
  language: 'zh' | 'en',
): string {
  if (language === 'en') {
    if (kind === 'contact') return 'Would you like me to open visitor registration?'
    if (kind === 'recording') return 'Would you like me to open live recording?'
    if (kind === 'transcript') return 'Would you like me to show the current conversation transcript?'
    return 'Would you like me to open the administrator verification screen for the result center?'
  }
  if (kind === 'contact') return '您是想打开访客登记信息表吗？'
  if (kind === 'recording') return '您是想打开实时录音吗？'
  if (kind === 'transcript') return '您是想查看当前对话记录吗？'
  return '您是想打开结果中心的管理员验证界面吗？'
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

async function openInteractionAndReply(
  controller: OfficeVoiceController,
  kind: InteractionWindowKind,
  signal: AbortSignal,
  source: string,
): Promise<boolean> {
  const lease = visitLeaseRegistry.current()
  const result = await openInteractionWindow({
    kind,
    conversationId: controller.conversationId,
    visitId: lease?.visitId ?? null,
    language: controller.language,
  })
  const reply = result.ok
    ? interactionReply(kind, controller.language)
    : controller.language === 'zh'
      ? '主屏幕交互面板没有成功打开，请刷新页面后再试。'
      : 'The main-display interaction panel did not open. Please refresh the page and try again.'
  await speakDirect(controller, reply, source, signal)
  return result.ok
}

async function recoverTurnState(
  controller: () => OfficeVoiceController,
  signal: AbortSignal,
): Promise<boolean> {
  let latest = controller()
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (signal.aborted) return false
    if (latest.panel === 'error') break
    await waitWithSignal(50, signal)
    latest = controller()
  }
  if (latest.panel !== 'error') return true

  console.warn('[RealtimeDiagnostics] recovering-controller-after-turn-error', {
    error: latest.error,
    conversationId: latest.conversationId,
  })
  latest.clearError()
  await latest.connect()

  for (let attempt = 0; attempt < 6; attempt += 1) {
    if (signal.aborted) return false
    const state = controller()
    if (state.panel === 'idle' && realtimeAgent.status().connected) {
      console.info('[RealtimeDiagnostics] controller-turn-recovery-complete', {
        recovered: true,
        panel: state.panel,
        connectionState: realtimeAgent.status().connectionState,
      })
      return true
    }
    if (state.panel === 'error' && attempt >= 2) break
    await waitWithSignal(50, signal)
  }

  console.error('[RealtimeDiagnostics] controller-turn-recovery-complete', {
    recovered: false,
    panel: controller().panel,
    connectionState: realtimeAgent.status().connectionState,
    error: controller().error,
  })
  return false
}

function desktopActionFor(
  target: 'teams' | 'onenote' | 'powerpoint' | 'music',
  action: Exclude<CommandAction, null>,
): DesktopAction {
  if (target === 'teams') return action === 'open' ? 'open_teams' : 'close_teams'
  if (target === 'onenote') return action === 'open' ? 'open_onenote' : 'close_onenote'
  if (target === 'powerpoint') {
    return action === 'open' ? 'open_powerpoint' : 'close_powerpoint'
  }
  return action === 'play' ? 'play_music' : 'stop_music'
}

async function executeDeterministicDesktopCommand(
  target: 'teams' | 'onenote' | 'powerpoint' | 'music',
  action: Exclude<CommandAction, null>,
  signal: AbortSignal,
): Promise<DesktopToolResult> {
  const exactAction = desktopActionFor(target, action)
  const response = await fetch(`${OFFICE_API_BASE}/api/desktop-command`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ action: exactAction }),
    signal,
  })
  if (!response.ok) {
    throw new Error(`Desktop command failed: ${response.status} ${await response.text()}`)
  }
  const payload = (await response.json()) as DesktopCommandResponse
  if (!payload.result) {
    throw new Error(`The Backend returned no result for exact action: ${exactAction}`)
  }
  console.info('[RealtimeDiagnostics] exact-desktop-command-result', {
    target,
    action,
    exactAction,
    toolName: payload.tool_name,
    ok: payload.result.ok,
  })
  return payload.result
}

function deterministicReply(
  target: CommandTarget,
  action: Exclude<CommandAction, null>,
  result: DesktopToolResult,
  language: 'zh' | 'en',
): string {
  const completed = result.ok === true
  if (!completed) {
    const detail = String(result.message ?? '').trim()
    return language === 'zh'
      ? `操作没有完成。${detail}`
      : `The action did not complete. ${detail}`
  }

  const placementConfirmed = result.data?.window_placement_verified === true
  if (language === 'en') {
    if (target === 'music') {
      if (action === 'play') {
        return placementConfirmed
          ? 'Music is playing and the media-player window is on display 2.'
          : 'Music is playing. The media-player window has been sent to display 2; you can adjust its size manually.'
      }
      return 'Music playback and the managed media player are closed.'
    }
    const app = target === 'teams'
      ? 'Teams'
      : target === 'onenote'
        ? 'OneNote'
        : 'PowerPoint'
    if (action === 'open') {
      return placementConfirmed
        ? `${app} is open on display 2.`
        : `${app} is open and its window has been sent to display 2; you can adjust its size manually.`
    }
    return target === 'powerpoint'
      ? 'PowerPoint is closed and unsaved changes were discarded.'
      : `${app} is closed.`
  }

  if (target === 'music') {
    if (action === 'play') {
      return placementConfirmed
        ? '音乐已经播放，媒体播放器窗口位于二号屏幕。'
        : '音乐已经播放，播放器窗口已发送到二号屏幕；窗口大小可以手动调整。'
    }
    return '音乐已经停止，受控媒体播放器也已关闭。'
  }
  const app = target === 'teams'
    ? 'Teams'
    : target === 'onenote'
      ? 'OneNote'
      : 'PowerPoint'
  if (action === 'open') {
    return placementConfirmed
      ? `${app} 已在二号屏幕打开。`
      : `${app} 已打开，窗口已发送到二号屏幕；窗口大小可以手动调整。`
  }
  if (target === 'powerpoint') return 'PowerPoint 已经关闭，未保存的修改已直接丢弃。'
  return `${app} 已经关闭。`
}

function isDeterministicDesktopCommand(
  target: CommandTarget | null,
  action: CommandAction,
): target is 'teams' | 'onenote' | 'powerpoint' | 'music' {
  return action !== null && (
    target === 'teams'
    || target === 'onenote'
    || target === 'powerpoint'
    || target === 'music'
  )
}

function currentPendingInteraction(visitId: string | null): PendingInteractionConfirmation | null {
  const pending = pendingInteractionConfirmation
  if (!pending) return null
  if (pending.expiresAt <= Date.now() || pending.visitId !== visitId) {
    pendingInteractionConfirmation = null
    return null
  }
  return pending
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
    const visitId = lease?.visitId ?? null
    publishSessionMessage({
      conversationId: current.conversationId,
      visitId,
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
      await speakDirect(current, clarification, 'bounded_command_clarification', signal)
      return { kind: 'heard', transcript }
    }

    if (isDeterministicDesktopCommand(recovered.target, recovered.action)) {
      const action = recovered.action as Exclude<CommandAction, null>
      const result = await executeDeterministicDesktopCommand(
        recovered.target,
        action,
        signal,
      )
      const reply = deterministicReply(
        recovered.target,
        action,
        result,
        recovered.language,
      )
      await speakDirect(current, reply, 'deterministic_desktop_command', signal)
      return { kind: 'heard', transcript }
    }

    const pending = currentPendingInteraction(visitId)
    if (pending && CONFIRM_PATTERN.test(transcript.trim())) {
      pendingInteractionConfirmation = null
      await openInteractionAndReply(
        current,
        pending.kind,
        signal,
        'semantic_interaction_confirmation',
      )
      return { kind: 'heard', transcript }
    }
    if (pending && REJECT_PATTERN.test(transcript.trim())) {
      pendingInteractionConfirmation = null
      await speakDirect(
        current,
        current.language === 'zh' ? '好的，已取消。' : 'Okay, cancelled.',
        'semantic_interaction_cancelled',
        signal,
      )
      return { kind: 'heard', transcript }
    }
    if (pending) pendingInteractionConfirmation = null

    const fastInteractionKind = matchInteractionWindowIntent(transcript)
    if (fastInteractionKind) {
      await openInteractionAndReply(
        current,
        fastInteractionKind,
        signal,
        'interaction_window_command',
      )
      return { kind: 'heard', transcript }
    }

    const semantic = await resolveSemanticInteractionIntent(
      transcript,
      current.language,
      signal,
    )
    if (semantic.intent !== 'none' && semantic.confidence >= 0.78) {
      await openInteractionAndReply(
        current,
        semantic.intent,
        signal,
        `semantic_interaction_${semantic.source}`,
      )
      return { kind: 'heard', transcript }
    }
    if (semantic.intent !== 'none' && semantic.confidence >= 0.55) {
      pendingInteractionConfirmation = {
        kind: semantic.intent,
        visitId,
        expiresAt: Date.now() + 20_000,
      }
      await speakDirect(
        current,
        interactionConfirmation(semantic.intent, current.language),
        'semantic_interaction_clarification',
        signal,
      )
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
