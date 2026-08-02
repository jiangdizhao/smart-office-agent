import type { OfficeVoiceController } from './useOfficeVoiceController'
import { realtimeAgent } from './realtimeAgentRuntime'
import { realtimeOfficeInterpreter } from './realtimeOfficeInterpreter'
import { voiceOutputManager } from './voiceOutputManager'

const nativeFetch = window.fetch.bind(window)
const TURN_SCOPED_PATHS = [
  '/api/general-chat',
  '/api/conversation-route',
  '/agent/turn',
  '/agent/office-turn',
  '/agent/tasks/',
  '/api/human-recordings/',
]

type ControllerGetter = () => OfficeVoiceController

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
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

  constructor() {
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
      void this.preempt('visitor_barge_in')
    })
    window.addEventListener('smartoffice:visit-activated', () => {
      this.reset('visit_activated')
    })
    window.addEventListener('smartoffice:visit-revoked', () => {
      this.reset('visit_revoked')
    })
  }

  attachController(getter: ControllerGetter): void {
    this.controllerGetter = getter
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

  async preferLatestUtterance(
    initial: string,
    visitSignal: AbortSignal,
  ): Promise<string> {
    let latest = initial
    for (let index = 0; index < 4; index += 1) {
      if (visitSignal.aborted) throw abortError('Visit ended while selecting the latest command.')
      const probe = new AbortController()
      const abortFromVisit = () => probe.abort()
      visitSignal.addEventListener('abort', abortFromVisit, { once: true })
      const timer = window.setTimeout(() => probe.abort(), 8)
      try {
        const next = await realtimeAgent.nextContinuousUtterance(probe.signal)
        if (next.trim()) latest = next
      } catch (error) {
        if (!(error instanceof Error && error.name === 'AbortError')) throw error
        break
      } finally {
        window.clearTimeout(timer)
        visitSignal.removeEventListener('abort', abortFromVisit)
      }
    }
    return latest
  }

  async waitForCancellation(): Promise<void> {
    await this.cancelPromise.catch(() => undefined)
  }

  async preempt(reason: string): Promise<void> {
    const previous = this.turnAbort
    if (previous && !previous.signal.aborted) previous.abort()
    this.epoch += 1

    const controller = this.controllerGetter?.()
    const taskId = controller?.taskId ?? null
    const shouldCancelTask = Boolean(controller?.active && taskId)
    console.info('[PreemptiveTurn] preempt', {
      reason,
      epoch: this.epoch,
      taskId,
      shouldCancelTask,
    })

    void voiceOutputManager.stop().catch(() => undefined)
    void realtimeAgent.stopOutput().catch(() => undefined)
    void realtimeOfficeInterpreter.shutdown().catch(() => undefined)
    void controller?.stopSpeaking().catch(() => undefined)

    if (shouldCancelTask && taskId) {
      this.cancelPromise = nativeFetch(
        `${window.location.origin.startsWith('http') ? '' : ''}${
          import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
        }/agent/tasks/${encodeURIComponent(taskId)}/cancel`,
        { method: 'POST', keepalive: true },
      ).then(() => undefined).catch(() => undefined)
      await this.cancelPromise
    }
  }

  reset(reason: string): void {
    this.turnAbort?.abort()
    this.turnAbort = null
    this.epoch += 1
    console.info('[PreemptiveTurn] reset', { reason, epoch: this.epoch })
  }
}

export const preemptiveTurnCoordinator = new PreemptiveTurnCoordinator()
