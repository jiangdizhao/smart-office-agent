import './nonBlockingTurnErrors.css'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
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

function requestUrl(input: RequestInfo | URL): URL | null {
  const source = input instanceof Request ? input.url : String(input)
  try {
    return new URL(source, window.location.href)
  } catch {
    return null
  }
}

function isTurnScoped(input: RequestInfo | URL): boolean {
  const url = requestUrl(input)
  return Boolean(url && TURN_SCOPED_PATHS.some((path) => url.pathname.startsWith(path)))
}

function requestDeadlineMs(input: RequestInfo | URL): number | null {
  const path = requestUrl(input)?.pathname ?? ''
  if (path.startsWith('/api/realtime/status')) return 5_000
  if (path.startsWith('/api/realtime/session')) return 15_000
  if (path.startsWith('/api/presentation/guided/start')) return 30_000
  if (path.startsWith('/api/presentation/guided/finish')) return 18_000
  if (path.startsWith('/api/presentation/session/answer')) return 15_000
  if (path.startsWith('/api/presentation/session/script')) return 8_000
  if (path.startsWith('/api/presentation/status')) return 6_000
  if (path.startsWith('/api/presentation/slideshow/')) return 10_000
  if (path.startsWith('/api/presentation/open')) return 20_000
  return null
}

function maxUtteranceMs(): number {
  const configured = Number(import.meta.env.VITE_REALTIME_MAX_UTTERANCE_MS)
  if (!Number.isFinite(configured)) return 20_000
  return Math.max(8_000, Math.min(60_000, Math.round(configured)))
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
  private vadLivenessTimer: number | null = null
  private vadItemId: string | null = null

  constructor() {
    // Turn-scoped requests inherit the current turn signal. Realtime connection and
    // presentation endpoints also receive hard network deadlines even before a turn
    // exists, so a half-open TCP request cannot hold the UI indefinitely.
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const scoped = isTurnScoped(input)
      const turnSignal = scoped ? this.turnAbort?.signal : undefined
      const sourceSignal = init?.signal ?? (input instanceof Request ? input.signal : undefined)
      const deadlineMs = requestDeadlineMs(input)
      const usableTurnSignal = turnSignal && !turnSignal.aborted ? turnSignal : undefined
      if (!usableTurnSignal && !sourceSignal && deadlineMs === null) {
        return await nativeFetch(input, init)
      }

      const requestAbort = new AbortController()
      const abort = () => requestAbort.abort()
      if (usableTurnSignal?.aborted || sourceSignal?.aborted) abort()
      usableTurnSignal?.addEventListener('abort', abort, { once: true })
      sourceSignal?.addEventListener('abort', abort, { once: true })
      const deadlineTimer = deadlineMs === null
        ? null
        : window.setTimeout(abort, deadlineMs)
      try {
        const [nextInput, nextInit] = requestWithSignal(input, init, requestAbort.signal)
        return await nativeFetch(nextInput, nextInit)
      } finally {
        if (deadlineTimer !== null) window.clearTimeout(deadlineTimer)
        usableTurnSignal?.removeEventListener('abort', abort)
        sourceSignal?.removeEventListener('abort', abort)
      }
    }

    window.addEventListener('smartoffice:realtime-vad-speech-started', (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null
      this.armVadLiveness(String(detail?.itemId ?? '').trim() || null)
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
    window.addEventListener('smartoffice:realtime-vad-speech-stopped', () => {
      this.clearVadLiveness()
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

    this.cancelPromise = interruption
    if (backgroundTaskActive) {
      window.dispatchEvent(new CustomEvent('smartoffice:background-task-preserved-during-barge-in', {
        detail: { taskId, reason, epoch: this.epoch },
      }))
    }

    await interruption
    await this.recoverToReady(reason)
  }

  private armVadLiveness(itemId: string | null): void {
    this.clearVadLiveness()
    this.vadItemId = itemId
    this.vadLivenessTimer = window.setTimeout(() => {
      void this.recoverStuckVad(itemId)
    }, maxUtteranceMs())
  }

  private clearVadLiveness(): void {
    if (this.vadLivenessTimer !== null) window.clearTimeout(this.vadLivenessTimer)
    this.vadLivenessTimer = null
    this.vadItemId = null
  }

  private async recoverStuckVad(expectedItemId: string | null): Promise<void> {
    if (this.vadItemId !== expectedItemId) return
    this.clearVadLiveness()
    console.error('[RealtimeDiagnostics] vad-liveness-timeout', {
      itemId: expectedItemId,
      maxUtteranceMs: maxUtteranceMs(),
      epoch: this.epoch,
    })
    await voiceOutputManager.stop('vad-liveness-timeout').catch(() => undefined)
    await realtimeAgent.stopOutput().catch(() => undefined)
    await realtimeAgent.stopContinuousCapture(false).catch(() => undefined)
    const controller = this.controllerGetter?.()
    const lease = visitLeaseRegistry.current()
    if (controller && lease && !lease.signal.aborted) {
      await realtimeAgent.startContinuousCapture(controller.language, lease.signal).catch(() => undefined)
    }
    window.dispatchEvent(new CustomEvent('smartoffice:realtime-vad-liveness-recovered', {
      detail: { itemId: expectedItemId, epoch: this.epoch },
    }))
    await this.recoverToReady('vad_liveness_timeout')
  }

  reset(reason: string): void {
    this.clearVadLiveness()
    this.turnAbort?.abort()
    this.turnAbort = null
    this.cancelPromise = Promise.resolve()
    this.epoch += 1
    console.info('[PreemptiveTurn] reset', { reason, epoch: this.epoch })
  }
}

export const preemptiveTurnCoordinator = new PreemptiveTurnCoordinator()
