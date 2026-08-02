import {
  publishSessionMessage,
} from '../interaction/sessionEventBus'
import {
  executeInteractionPanelCommand,
  type InteractionVoiceCommand,
} from '../interaction/interactionPanelCommandBus'
import {
  resolveInteractionVoiceCommand,
} from '../interaction/interactionVoiceCommandInterpreter'
import {
  commandClarification,
  recoverCommandTranscript,
  type CommandAction,
  type CommandTarget,
} from '../voice/commandSpeechRecovery'
import {
  preemptiveTurnCoordinator,
} from '../voice/preemptiveTurnCoordinator'
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
  command: InteractionVoiceCommand
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
  let latest = controller()
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (signal.aborted) return false
    if (latest.panel === 'error') break
    await waitWithSignal(50, signal)
    latest = controller()
  }
  if (latest.panel !== 'error') return true

  latest.clearError()
  await latest.connect()
  for (let attempt = 0; attempt < 6; attempt += 1) {
    if (signal.aborted) return false
    const state = controller()
    if (state.panel === 'idle' && realtimeAgent.status().connected) return true
    if (state.panel === 'error' && attempt >= 2) break
    await waitWithSignal(50, signal)
  }
  return false
}

function desktopActionFor(
  target: 'teams' | 'onenote' | 'powerpoint' | 'music',
  action: Exclude<CommandAction, null>,
): DesktopAction {
  if (target === 'teams') return action === 'open' ? 'open_teams' : 'close_teams'
  if (target === 'onenote') return action === 'open' ? 'open_onenote' : 'close_onenote'
  if (target === 'powerpoint') return action === 'open' ? 'open_powerpoint' : 'close_powerpoint'
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
  if (!payload.result) throw new Error(`The Backend returned no result for ${exactAction}.`)
  return payload.result
}

function deterministicReply(
  target: CommandTarget,
  action: Exclude<CommandAction, null>,
  result: DesktopToolResult,
  language: 'zh' | 'en',
): string {
  if (result.ok !== true) {
    const detail = String(result.message ?? '').trim()
    return language === 'zh'
      ? `操作没有完成。${detail}`
      : `The action did not complete. ${detail}`
  }
  const placementConfirmed = result.data?.window_placement_verified === true
  if (language === 'en') {
    if (target === 'music') {
      if (action === 'play') return placementConfirmed
        ? 'Music is playing and the media-player window is on display 2.'
        : 'Music is playing. The player window was sent to display 2.'
      return 'Music playback and the managed media player are closed.'
    }
    const app = target === 'teams' ? 'Teams' : target === 'onenote' ? 'OneNote' : 'PowerPoint'
    if (action === 'open') return placementConfirmed
      ? `${app} is open on display 2.`
      : `${app} is open and its window was sent to display 2.`
    return target === 'powerpoint'
      ? 'PowerPoint is closed and unsaved changes were discarded.'
      : `${app} is closed.`
  }
  if (target === 'music') {
    if (action === 'play') return placementConfirmed
      ? '音乐已经播放，媒体播放器窗口位于二号屏幕。'
      : '音乐已经播放，播放器窗口已发送到二号屏幕。'
    return '音乐已经停止，受控媒体播放器也已关闭。'
  }
  const app = target === 'teams' ? 'Teams' : target === 'onenote' ? 'OneNote' : 'PowerPoint'
  if (action === 'open') return placementConfirmed
    ? `${app} 已在二号屏幕打开。`
    : `${app} 已打开，窗口已发送到二号屏幕。`
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

function interactionConfirmation(
  command: InteractionVoiceCommand,
  language: 'zh' | 'en',
): string {
  if (language === 'en') {
    if (command.target === 'contact') return 'Would you like me to open visitor registration?'
    if (command.target === 'recording') return 'Would you like me to open live recording?'
    if (command.target === 'transcript') return 'Would you like me to show the current transcript?'
    return 'Would you like me to open the administrator verification screen?'
  }
  if (command.target === 'contact') return '您是想打开访客登记信息表吗？'
  if (command.target === 'recording') return '您是想打开实时录音吗？'
  if (command.target === 'transcript') return '您是想查看当前对话记录吗？'
  return '您是想打开结果中心的管理员验证界面吗？'
}

function interactionReply(
  command: InteractionVoiceCommand,
  status: 'completed' | 'failed' | 'auth_required' | 'stale',
  message: string,
  language: 'zh' | 'en',
): string {
  if (status === 'auth_required') {
    return language === 'zh'
      ? '结果中心已打开，请先在屏幕上输入管理员密码。'
      : 'The result center is open. Enter the administrator password on the display first.'
  }
  if (status !== 'completed') {
    return language === 'zh'
      ? `界面操作没有完成。${message}`
      : `The panel action did not complete. ${message}`
  }

  if (language === 'en') {
    if (command.action === 'close') return 'The panel is closed.'
    if (command.target === 'contact') return 'Visitor registration is open beside Sara.'
    if (command.target === 'transcript') {
      return command.action === 'refresh'
        ? 'The current transcript has been refreshed.'
        : 'The current transcript is open beside Sara.'
    }
    if (command.target === 'recording') {
      if (command.action === 'start') return 'Recording has started.'
      if (command.action === 'stop_save') return 'Recording stopped and the audio file was saved.'
      if (command.action.includes('summarize')) return 'The recording was saved and its summary was generated.'
      if (command.action === 'download') return 'The recording download has started.'
      return 'Live recording is open beside Sara.'
    }
    if (command.action === 'open') return 'The administrator verification screen is open.'
    if (command.action === 'show_contacts') return 'The contact records tab is open.'
    if (command.action === 'show_recordings') return 'The recordings tab is open.'
    if (command.action === 'show_transcript') return 'The current-conversation tab is open.'
    if (command.action === 'export_csv') return 'The contact CSV export has started.'
    if (command.action === 'play_latest_recording') return 'The latest recording is playing.'
    if (command.action === 'open_directory') return 'The recording output folder has been requested.'
    return 'The result center has been refreshed.'
  }

  if (command.action === 'close') return '界面已经关闭。'
  if (command.target === 'contact') return '我已经在 Sara 左侧打开登记信息表。'
  if (command.target === 'transcript') {
    return command.action === 'refresh'
      ? '当前对话记录已经刷新。'
      : '我已经在 Sara 左侧打开当前对话记录。'
  }
  if (command.target === 'recording') {
    if (command.action === 'start') return '录音已经开始。'
    if (command.action === 'stop_save') return '录音已经停止并保存。'
    if (command.action.includes('summarize')) return '录音已经保存，录音总结也已经生成。'
    if (command.action === 'download') return '录音下载已经开始。'
    return '我已经在 Sara 左侧打开实时录音界面。'
  }
  if (command.action === 'open') return '我已经打开管理员验证界面，请在屏幕上输入管理员密码。'
  if (command.action === 'show_contacts') return '结果中心已经切换到登记信息。'
  if (command.action === 'show_recordings') return '结果中心已经切换到录音文件。'
  if (command.action === 'show_transcript') return '结果中心已经切换到当前对话。'
  if (command.action === 'export_csv') return '联系人 CSV 已经开始导出。'
  if (command.action === 'play_latest_recording') return '最新录音已经开始播放。'
  if (command.action === 'open_directory') return '已经请求打开录音保存目录。'
  return '结果中心已经刷新。'
}

async function executeInteractionAndReply(
  controller: OfficeVoiceController,
  command: InteractionVoiceCommand,
  signal: AbortSignal,
  source: string,
): Promise<void> {
  const lease = visitLeaseRegistry.current()
  const result = await executeInteractionPanelCommand({
    ...command,
    conversationId: controller.conversationId,
    visitId: lease?.visitId ?? null,
    language: controller.language,
  }, signal)
  const reply = interactionReply(
    command,
    result.status,
    result.message,
    controller.language,
  )
  await speakDirect(controller, reply, source, signal)
}

export async function captureAutomaticRealtimeTurn(
  controller: () => OfficeVoiceController,
  visitSignal: AbortSignal,
): Promise<AutomaticVoiceTurnResult> {
  if (visitSignal.aborted) return { kind: 'aborted' }
  preemptiveTurnCoordinator.attachController(controller)
  let turnEpoch: number | null = null
  let turnSignal: AbortSignal = visitSignal
  let transcript = ''

  try {
    const current = controller()
    await realtimeAgent.startContinuousCapture(current.language, visitSignal)
    const firstTranscript = await realtimeAgent.nextContinuousUtterance(visitSignal)
    const rawTranscript = await preemptiveTurnCoordinator.preferLatestUtterance(
      firstTranscript,
      visitSignal,
    )
    if (visitSignal.aborted) return { kind: 'aborted' }

    const turn = preemptiveTurnCoordinator.beginTurn(visitSignal)
    turnEpoch = turn.epoch
    turnSignal = turn.signal

    const recovered = recoverCommandTranscript(rawTranscript, current.language)
    transcript = recovered.normalized
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
        turnEpoch,
      },
    }))

    const clarification = commandClarification(recovered)
    if (clarification) {
      await speakDirect(current, clarification, 'bounded_command_clarification', turnSignal)
      return { kind: 'heard', transcript }
    }

    if (isDeterministicDesktopCommand(recovered.target, recovered.action)) {
      const action = recovered.action as Exclude<CommandAction, null>
      const result = await executeDeterministicDesktopCommand(
        recovered.target,
        action,
        turnSignal,
      )
      if (!preemptiveTurnCoordinator.isCurrent(turnEpoch)) return { kind: 'aborted' }
      await speakDirect(
        current,
        deterministicReply(recovered.target, action, result, recovered.language),
        'deterministic_desktop_command',
        turnSignal,
      )
      return { kind: 'heard', transcript }
    }

    const pending = currentPendingInteraction(visitId)
    if (pending && CONFIRM_PATTERN.test(transcript.trim())) {
      pendingInteractionConfirmation = null
      await executeInteractionAndReply(
        current,
        pending.command,
        turnSignal,
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
        turnSignal,
      )
      return { kind: 'heard', transcript }
    }
    if (pending) pendingInteractionConfirmation = null

    const interaction = await resolveInteractionVoiceCommand(
      transcript,
      current.language,
      turnSignal,
    )
    if (interaction.command && interaction.confidence >= 0.78) {
      await executeInteractionAndReply(
        current,
        interaction.command,
        turnSignal,
        `interaction_command_${interaction.source}`,
      )
      return { kind: 'heard', transcript }
    }
    if (interaction.command && interaction.confidence >= 0.55) {
      pendingInteractionConfirmation = {
        command: interaction.command,
        visitId,
        expiresAt: Date.now() + 20_000,
      }
      await speakDirect(
        current,
        interactionConfirmation(interaction.command, current.language),
        'semantic_interaction_clarification',
        turnSignal,
      )
      return { kind: 'heard', transcript }
    }

    await preemptiveTurnCoordinator.waitForCancellation()
    if (!preemptiveTurnCoordinator.isCurrent(turnEpoch)) return { kind: 'aborted' }
    await current.submit(transcript, 'voice')
    if (!preemptiveTurnCoordinator.isCurrent(turnEpoch)) return { kind: 'aborted' }
    const healthy = await recoverTurnState(controller, turnSignal)
    if (!healthy) {
      return {
        kind: 'error',
        message: 'The voice turn failed and the controller could not recover automatically.',
      }
    }
    return { kind: 'heard', transcript }
  } catch (error) {
    const aborted = error instanceof Error && error.name === 'AbortError'
    if (aborted || turnSignal.aborted || visitSignal.aborted) {
      if (visitSignal.aborted) {
        await realtimeAgent.stopContinuousCapture(true).catch(() => undefined)
      }
      return { kind: 'aborted' }
    }

    const recovered = await recoverTurnState(controller, visitSignal).catch(() => false)
    return {
      kind: 'error',
      message: recovered
        ? `${errorText(error)} The controller recovered and listening will continue.`
        : errorText(error),
    }
  } finally {
    if (turnEpoch !== null) preemptiveTurnCoordinator.finishTurn(turnEpoch)
  }
}
