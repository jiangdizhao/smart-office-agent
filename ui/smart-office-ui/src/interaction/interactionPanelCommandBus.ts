import {
  INTERACTION_PANEL_COMMAND_EVENT,
  INTERACTION_PANEL_RESULT_EVENT,
  closeInteractionPanel,
  currentInteractionPanel,
  openInteractionWindow,
  type InteractionPanelCommand,
  type InteractionPanelCommandResult,
  type InteractionWindowKind,
} from '../display/multiScreenWindowManager'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'

export type RecordingPanelAction =
  | 'open'
  | 'start'
  | 'stop_save'
  | 'summarize'
  | 'stop_save_summarize'
  | 'download'
  | 'close'

export type TranscriptPanelAction = 'open' | 'refresh' | 'close'
export type MeetingPanelAction = 'open' | 'close'

export type ResultPanelAction =
  | 'open'
  | 'show_contacts'
  | 'show_recordings'
  | 'show_transcript'
  | 'refresh'
  | 'export_csv'
  | 'play_latest_recording'
  | 'open_directory'
  | 'close'

export type ContactPanelAction = 'open' | 'close'

export type InteractionVoiceCommand =
  | { target: 'contact'; action: ContactPanelAction }
  | { target: 'meeting'; action: MeetingPanelAction }
  | { target: 'recording'; action: RecordingPanelAction }
  | { target: 'transcript'; action: TranscriptPanelAction }
  | { target: 'results'; action: ResultPanelAction }

export type ExecuteInteractionCommandRequest = InteractionVoiceCommand & {
  conversationId: string
  visitId: string | null
  language: VoiceLanguage
}

function abortError(): Error {
  const error = new Error('Interaction panel command was superseded.')
  error.name = 'AbortError'
  return error
}

function immediateResult(
  request: ExecuteInteractionCommandRequest,
  message: string,
): InteractionPanelCommandResult {
  return {
    commandId: crypto.randomUUID(),
    panelInstanceId: currentInteractionPanel()?.panelInstanceId ?? '',
    visitId: request.visitId,
    target: request.target,
    action: request.action,
    ok: true,
    status: 'completed',
    message,
  }
}

export async function executeInteractionPanelCommand(
  request: ExecuteInteractionCommandRequest,
  signal?: AbortSignal,
): Promise<InteractionPanelCommandResult> {
  if (signal?.aborted) throw abortError()

  if (request.action === 'close') {
    const active = currentInteractionPanel()
    if (!active || active.kind !== request.target) {
      return immediateResult(request, 'The requested interaction panel is already closed.')
    }
    closeInteractionPanel()
    return immediateResult(request, 'The interaction panel was closed.')
  }

  const opened = await openInteractionWindow({
    kind: request.target as InteractionWindowKind,
    conversationId: request.conversationId,
    visitId: request.visitId,
    language: request.language,
  })
  if (!opened.ok) {
    return {
      commandId: crypto.randomUUID(),
      panelInstanceId: opened.request.panelInstanceId ?? '',
      visitId: request.visitId,
      target: request.target,
      action: request.action,
      ok: false,
      status: 'failed',
      message: opened.message,
    }
  }

  if (request.action === 'open') {
    return {
      commandId: crypto.randomUUID(),
      panelInstanceId: opened.request.panelInstanceId ?? '',
      visitId: request.visitId,
      target: request.target,
      action: request.action,
      ok: true,
      status: 'completed',
      message: opened.message,
    }
  }

  const panelInstanceId = String(opened.request.panelInstanceId ?? '')
  if (!panelInstanceId) {
    throw new Error('The interaction panel returned no instance identifier.')
  }
  const command: InteractionPanelCommand = {
    commandId: crypto.randomUUID(),
    panelInstanceId,
    visitId: request.visitId,
    target: request.target,
    action: request.action,
  }

  return await new Promise<InteractionPanelCommandResult>((resolve, reject) => {
    let settled = false
    const timeoutMs = request.action.includes('summarize') ? 90_000 : 45_000
    const cleanup = () => {
      window.clearTimeout(timer)
      window.removeEventListener(INTERACTION_PANEL_RESULT_EVENT, onResult as EventListener)
      signal?.removeEventListener('abort', onAbort)
    }
    const finish = (
      result?: InteractionPanelCommandResult,
      error?: Error,
    ) => {
      if (settled) return
      settled = true
      cleanup()
      if (error) reject(error)
      else if (result) resolve(result)
    }
    const onResult = (event: Event) => {
      const result = event instanceof CustomEvent
        ? event.detail as InteractionPanelCommandResult
        : null
      if (!result || result.commandId !== command.commandId) return
      finish(result)
    }
    const onAbort = () => finish(undefined, abortError())
    const timer = window.setTimeout(() => {
      finish(undefined, new Error(`Interaction panel command timed out: ${request.target}/${request.action}`))
    }, timeoutMs)

    window.addEventListener(INTERACTION_PANEL_RESULT_EVENT, onResult as EventListener)
    signal?.addEventListener('abort', onAbort, { once: true })
    window.dispatchEvent(
      new CustomEvent<InteractionPanelCommand>(INTERACTION_PANEL_COMMAND_EVENT, {
        detail: command,
      }),
    )
  })
}
