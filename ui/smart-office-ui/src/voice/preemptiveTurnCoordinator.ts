import './nonBlockingTurnErrors.css'
import type { OfficeVoiceController } from './useOfficeVoiceController'
import { realtimeAgent } from './realtimeAgentRuntime'
import { realtimeOfficeInterpreter } from './realtimeOfficeInterpreter'
import { voiceOutputManager } from './voiceOutputManager'

const nativeFetch = window.fetch.bind(window)
const TURN_SCOPED_PATHS = [
  '/api/semantic-route',
  '/api/general-chat',
  '/api/conversation-route',
  '/api/presentation',
  '/agent/turn',
  '/agent/office-turn',
]
const RECOVERY_POLL_MS = 50
const RECOVERY_ATTEMPTS = 20
const PASSIVE_ERROR_RECOVERY_MS = 250

type ControllerGetter = () => OfficeVoiceController

type RuntimeQueueInternals = {
  utteranceQueue?: string[]
}

export class SupersededTurnError extends Error {
  constructor(message = 'The turn was superseded by a newer visitor command.') {
    super(message)
    this.name = 'AbortError'
  }
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function isTurnScoped(input: RequestInfo | URL): boolean {
  const source = input instanceof Request ? input.url : String(input)
  let url: URL
  try {
    url = new URL(source, window.location.href)
  } catch {
    return false
  }
  return TURN_SCOPED_PATHS.some((path) => url.pathname.startsWith(path))
}

function requestWithSignal(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  signal: AbortSignal,
): [RequestInfo | URL, RequestInit | undefined] {
  if (input instanceof Request) {
    return [new Request(input, { ...init, signal }), undefined]
  }
  return [input, { ...init, signal }]
}

class PreemptiveTurnCoordinator {
  private epoch = 0
  private turnAbort: AbortController | null = null
  private controllerGetter: ControllerGetter | null = null
  private cancelPromise: Promise<void> = Promise.resolve()
  private recovering: Promise<boolean> | null = null

  constructor() {
    // Only requests that belong to one conversational turn inherit the turn signal.
    // Presentation control and grounded slide answers are included so a new spoken
    // command cancels the old HTTP request as well as the old audio output.
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const turnSignal = this.turnAbort?.signal
      if (!turnSignal || turnSignal.aborted || !isTurnScoped(input)) {
        return await nativeFetch(input, init)
      }
      const requestAbort = new AbortController()
      const sourceSignal = init?.signal ?? (input instanceof Request ? input.signal : undefined)
      const abort = () => requestAbort.abort()
      turnSignal.addEventListener('abort', abort, { once: true })
      sourceSignal?.addEventListener('abort', abort, { once: true })
      try {
        const [nextInput, nextInit] = requestWithSignal(input, init, requestAbort.signal)
        return await nativeFetch(nextInput, nextInit)
      } finally {
        turnSignal.removeEventListener('abort', abort)
        sourceSignal?.removeEventListener('abort', abort)
      }
    }

    window.addEventListener('smartoffice:realtime-vad-speech-started', () => {
      const controller = this.controllerGetter?.()
      const hasActiveTurn = Boolean(this.turnAbort && !this.turnAbort.signal.aborted)
      const shouldPreempt = Boolean(
        hasActiveTurn
        || controller?.runtime.outputActive
        || controller?.panel === 'processing'
        || controller?.panel === 'speaking'
        || controller?.active,
      )
      if (shouldPreempt) void this.preempt('visitor_barge_in')
    })
    window.addEventListener('smartoffice:visit-activated', () => {
      this.reset('visit_activated')
    })
    window.addEventListener('smartoffice:visit-revoked', () => {
      this.reset('visit_revoked')
    })

    // A failed conversational turn must never become a user-facing gate. The
    // Controller still records diagnostics internally, but the runtime clears the
    // transient error state and returns to idle without asking the visitor to retry.
    window.setInterval(() => {
      const controller = this.controllerGetter?.()
      if (controller?.panel === 'error') {
        void this.recoverToReady('passive_nonblocking_turn_error')
      }
    }, PASSIVE_ERROR_RECOVERY_MS)
  }

  attachController(getter: ControllerGetter): void {
    this.controllerGetter = getter
    if (getter().panel === 'error') {
      void this.recoverToReady('controller_attached_with_error')
    }
  }

  beginTurn(visitSignal: AbortSignal): { epoch: number; signal: AbortSignal } {
    this.turnAbort?.abort()
    this.epoch += 1
    const controller = new AbortController()
    const abortFromVisit = () => controller.abort()
    visitSignal.addEventListener('abort', abortFromVisit, { once: true })
    controller.signal.addEventListener('abort', () => {
      visitSignal.removeEventListener('abort', abortFromVisit)
    }, { once: true })
    this.turnAbort = controller
    return { epoch: this.epoch, signal: controller.signal }
  }

  isCurrent(epoch: number): boolean {
    return this.epoch === epoch && !this.turnAbort?.signal.aborted
  }

  finishTurn(epoch: number): void {
    if (this.epoch !== epoch) return
    this.turnAbort = null
  }

  takeLatestUtterance(initial: string): string {
    const internals = realtimeAgent as unknown as RuntimeQueueInternals
    const queue = internals.utteranceQueue
    if (!Array.isArray(queue) || queue.length === 0) return initial
    const latest = String(queue[queue.length - 1] ?? '').trim()
    queue.splice(0)
    return latest || initial
  }

  async waitForCancellation(): Promise<void> {
    await this.cancelPromise.catch(() => undefined)
  }

  async recoverToReady(reason: string): Promise<boolean> {
    if (this.recovering) return await this.recovering
    const operation = this.recoverController(reason)
    this.recovering = operation
    try {
      return await operation
    } finally {
      if (this.recovering === operation) this.recovering = null
    }
  }

  private async recoverController(reason: string): Promise<boolean> {
    for (let attempt = 0; attempt < RECOVERY_ATTEMPTS; attempt += 1) {
      const controller = this.controllerGetter?.()
      if (!controller) return true

      if (controller.panel === 'error') {
        console.warn('[PreemptiveTurn] transient-turn-error-cleared', {
          reason,
          diagnostic: controller.error,
          epoch: this.epoch,
        })
        controller.clearError()
        await controller.stopSpeaking().catch(() => undefined)
      } else if (controller.panel === 'processing' || controller.panel === 'speaking') {
        await controller.stopSpeaking().catch(() => undefined)
      }

      const latest = this.controllerGetter?.()
      if (!latest) return true
      if (!realtimeAgent.status().connected) {
        await latest.connect().catch(() => undefined)
      }

      const after = this.controllerGetter?.()
      if (
        after
        && after.panel === 'idle'
        && !after.listening
        && realtimeAgent.status().connected
      ) {
        console.info('[PreemptiveTurn] recovery-complete', {
          reason,
          attempt: attempt + 1,
          epoch: this.epoch,
          backgroundTaskActive: Boolean(after.active),
        })
        window.dispatchEvent(new CustomEvent('smartoffice:turn-ready', {
          detail: { reason, epoch: this.epoch, backgroundTaskActive: Boolean(after.active) },
        }))
        return true
      }
      await wait(RECOVERY_POLL_MS)
    }

    const controller = this.controllerGetter?.()
    console.error('[PreemptiveTurn] recovery-failed', {
      reason,
      epoch: this.epoch,
      panel: controller?.panel ?? 'unavailable',
      connected: realtimeAgent.status().connected,
      backgroundTaskActive: Boolean(controller?.active),
    })
    return false
  }

  async preempt(reason: string): Promise<void> {
    const previous = this.turnAbort
    const controller = this.controllerGetter?.()
    const taskId = controller?.taskId ?? null
    const hadTurn = Boolean(previous && !previous.signal.aborted)
    const backgroundTaskActive = Boolean(controller?.active && taskId)

    if (hadTurn) previous?.abort()
    this.turnAbort = null
    this.epoch += 1

    console.info('[PreemptiveTurn] preempt', {
      reason,
      epoch: this.epoch,
      taskId,
      backgroundTaskActive,
      backgroundTaskPreserved: true,
      hadTurn,
      panel: controller?.panel ?? null,
    })

    // Barge-in has the highest conversational priority: stop audio and the current
    // LLM/Office/presentation request immediately. It must not cancel an already
    // accepted background Office task. Task cancellation is explicit only.
    const interruption = Promise.allSettled([
      voiceOutputManager.stop(reason),
      realtimeAgent.stopOutput(),
      controller?.stopSpeaking() ?? Promise.resolve(),
      controller?.panel === 'processing'
        ? realtimeOfficeInterpreter.shutdown()
        : Promise.resolve(),
    ]).then(() => undefined)

    // Every later turn must wait for the real cancellation sequence. The previous
    // implementation exposed an already-resolved Promise here, allowing the old
    // answer, Office interpreter and the new command to overlap.
    this.cancelPromise = interruption
    if (backgroundTaskActive) {
      window.dispatchEvent(new CustomEvent('smartoffice:background-task-preserved-during-barge-in', {
        detail: { taskId, reason, epoch: this.epoch },
      }))
    }

    await interruption
    await this.recoverToReady(reason)
  }

  reset(reason: string): void {
    this.turnAbort?.abort()
    this.turnAbort = null
    this.cancelPromise = Promise.resolve()
    this.epoch += 1
    console.info('[PreemptiveTurn] reset', { reason, epoch: this.epoch })
  }
}

export const preemptiveTurnCoordinator = new PreemptiveTurnCoordinator()
