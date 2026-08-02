import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'

const MAX_UTTERANCE_MS = boundedEnv(
  'VITE_REALTIME_MAX_UTTERANCE_MS',
  7_000,
  3_000,
  15_000,
)
const UTTERANCE_WAIT_TIMEOUT_MS = boundedEnv(
  'VITE_REALTIME_UTTERANCE_WAIT_TIMEOUT_MS',
  15_000,
  5_000,
  45_000,
)
const WATCHDOG_VAD_THRESHOLD = boundedEnv(
  'VITE_REALTIME_VAD_THRESHOLD',
  0.78,
  0,
  1,
)
const WATCHDOG_VAD_SILENCE_MS = boundedEnv(
  'VITE_REALTIME_VAD_SILENCE_MS',
  500,
  250,
  3_000,
)
const WATCHDOG_VAD_PREFIX_MS = boundedEnv(
  'VITE_REALTIME_VAD_PREFIX_MS',
  420,
  100,
  1_500,
)

type RuntimeInternals = {
  handleServerEvent: (message: MessageEvent<string>) => void
  enqueueUtterance: (text: string) => void
  safeSend: (event: Record<string, unknown>) => void
  utteranceQueue: string[]
  continuousCaptureActive: boolean
  vadSpeechDetected: boolean
}

function boundedEnv(
  name: string,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const value = Number(import.meta.env[name])
  if (!Number.isFinite(value)) return fallback
  return Math.max(minimum, Math.min(maximum, value))
}

function timeoutError(message: string): Error {
  const error = new Error(message)
  error.name = 'UtteranceLivenessError'
  return error
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

let installed = false
let activeLanguage: VoiceLanguage = 'zh'
let speechItemId = ''
let speechEpoch = 0
let maxSpeechTimer: number | null = null
const forcedItems = new Set<string>()

function clearMaxSpeechTimer(): void {
  if (maxSpeechTimer !== null) window.clearTimeout(maxSpeechTimer)
  maxSpeechTimer = null
}

function parseEvent(message: MessageEvent<string>): Record<string, unknown> | null {
  try {
    return objectValue(JSON.parse(message.data) as unknown)
  } catch {
    return null
  }
}

function forceSpeechBoundary(
  internals: RuntimeInternals,
  itemId: string,
  epoch: number,
): void {
  if (
    !internals.continuousCaptureActive
    || !internals.vadSpeechDetected
    || epoch !== speechEpoch
  ) return
  const resolvedItemId = itemId || `watchdog-${Date.now()}`
  forcedItems.add(resolvedItemId)
  console.warn('[RealtimeDiagnostics] vad-max-utterance-forced-boundary', {
    itemId: resolvedItemId,
    maxUtteranceMs: MAX_UTTERANCE_MS,
    language: activeLanguage,
  })
  window.dispatchEvent(new CustomEvent('smartoffice:realtime-vad-watchdog', {
    detail: {
      itemId: resolvedItemId,
      reason: 'max_utterance',
      maxUtteranceMs: MAX_UTTERANCE_MS,
    },
  }))

  // Server VAD occasionally remains in speech_started indefinitely in noisy rooms.
  // Commit the bounded audio accumulated so far and feed the existing transcription
  // path a synthetic endpoint. The later duplicate server endpoint is suppressed,
  // while this synthetic endpoint must continue into the original transcription path.
  try {
    internals.safeSend({ type: 'input_audio_buffer.commit' })
  } catch {
    // The utterance waiter watchdog will recover the loop if commit is rejected.
  }
  internals.handleServerEvent(new MessageEvent<string>('message', {
    data: JSON.stringify({
      type: 'input_audio_buffer.speech_stopped',
      item_id: resolvedItemId,
      audio_end_ms: null,
      metadata: { source: 'client_endpoint_watchdog' },
    }),
  }))
}

function applyBoundedVad(internals: RuntimeInternals): void {
  internals.safeSend({
    type: 'session.update',
    session: {
      type: 'realtime',
      audio: {
        input: {
          noise_reduction: { type: 'near_field' },
          turn_detection: {
            type: 'server_vad',
            threshold: WATCHDOG_VAD_THRESHOLD,
            prefix_padding_ms: WATCHDOG_VAD_PREFIX_MS,
            silence_duration_ms: WATCHDOG_VAD_SILENCE_MS,
            create_response: false,
            interrupt_response: false,
          },
        },
      },
    },
  })
}

export function installRealtimeLivenessPatch(): void {
  if (installed) return
  installed = true

  const internals = realtimeAgent as unknown as RuntimeInternals
  const originalHandle = internals.handleServerEvent.bind(realtimeAgent)
  const originalEnqueue = internals.enqueueUtterance.bind(realtimeAgent)
  const originalStart = realtimeAgent.startContinuousCapture.bind(realtimeAgent)
  const originalStop = realtimeAgent.stopContinuousCapture.bind(realtimeAgent)
  const originalNext = realtimeAgent.nextContinuousUtterance.bind(realtimeAgent)

  internals.enqueueUtterance = (text: string) => {
    const clean = text.trim()
    if (!clean) return
    // Capacity-one latest-command slot. A newly completed transcript supersedes every
    // older transcript that has not entered execution yet.
    internals.utteranceQueue.splice(0)
    originalEnqueue(clean)
    if (internals.utteranceQueue.length > 1) {
      internals.utteranceQueue.splice(0, internals.utteranceQueue.length - 1)
    }
    window.dispatchEvent(new CustomEvent('smartoffice:latest-utterance-ready', {
      detail: { transcript: clean, queuedAt: Date.now() },
    }))
  }

  internals.handleServerEvent = (message: MessageEvent<string>) => {
    const event = parseEvent(message)
    const type = String(event?.type ?? '')
    const itemId = String(event?.item_id ?? '')
    const metadata = objectValue(event?.metadata)
    const watchdogSynthetic = metadata?.source === 'client_endpoint_watchdog'

    if (type === 'input_audio_buffer.speech_started') {
      // Let the original runtime apply barge-in grace and echo suppression first.
      // Only an accepted visitor segment sets vadSpeechDetected=true and receives a
      // maximum-duration watchdog.
      originalHandle(message)
      if (internals.vadSpeechDetected) {
        clearMaxSpeechTimer()
        speechEpoch += 1
        const epoch = speechEpoch
        speechItemId = itemId
        maxSpeechTimer = window.setTimeout(
          () => forceSpeechBoundary(internals, speechItemId, epoch),
          MAX_UTTERANCE_MS,
        )
      }
      return
    }

    if (type === 'input_audio_buffer.speech_stopped') {
      clearMaxSpeechTimer()
      speechEpoch += 1
      speechItemId = ''
      if (!watchdogSynthetic && itemId && forcedItems.delete(itemId)) {
        console.info('[RealtimeDiagnostics] duplicate-server-endpoint-suppressed', {
          itemId,
        })
        return
      }
      originalHandle(message)
      return
    }

    originalHandle(message)
  }

  realtimeAgent.startContinuousCapture = async (language, signal) => {
    activeLanguage = language
    await originalStart(language, signal)
    applyBoundedVad(internals)
  }

  realtimeAgent.stopContinuousCapture = async (releaseMicrophone = true) => {
    clearMaxSpeechTimer()
    speechEpoch += 1
    speechItemId = ''
    forcedItems.clear()
    await originalStop(releaseMicrophone)
  }

  realtimeAgent.nextContinuousUtterance = async (signal) => {
    if (signal?.aborted) throw new DOMException('Operation aborted.', 'AbortError')
    const waiterAbort = new AbortController()
    const abortFromParent = () => waiterAbort.abort()
    signal?.addEventListener('abort', abortFromParent, { once: true })
    let timer: number | null = null
    let timedOut = false

    try {
      return await new Promise<string>((resolve, reject) => {
        const armWatchdog = () => {
          timer = window.setTimeout(() => {
            if (!realtimeAgent.status().speechDetected) {
              // Normal silence is not an error. Keep the one active waiter alive and
              // re-arm until a speech segment begins or the Visit is revoked.
              armWatchdog()
              return
            }
            timedOut = true
            waiterAbort.abort()
            reject(timeoutError(
              `No complete utterance was produced within ${UTTERANCE_WAIT_TIMEOUT_MS} ms.`,
            ))
          }, UTTERANCE_WAIT_TIMEOUT_MS)
        }
        armWatchdog()

        void originalNext(waiterAbort.signal).then(resolve).catch((error) => {
          if (!timedOut) reject(error)
        })
      })
    } finally {
      if (timer !== null) window.clearTimeout(timer)
      signal?.removeEventListener('abort', abortFromParent)
    }
  }

  window.addEventListener('smartoffice:visit-revoked', clearMaxSpeechTimer)
}

installRealtimeLivenessPatch()
