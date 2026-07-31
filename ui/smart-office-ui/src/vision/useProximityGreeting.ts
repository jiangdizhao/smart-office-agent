import { useEffect, useRef, useState } from 'react'
import {
  OFFICE_API_BASE,
  type OfficeVoiceController,
} from '../voice/useOfficeVoiceController'
import { realtimeAgent } from '../voice/realtimeAgentRuntime'
import { voiceOutputManager } from '../voice/voiceOutputManager'
import {
  ProximityFaceMonitor,
  type ProximityDetection,
  type ProximityDetectorStatus,
} from './proximityFaceMonitor'
import { captureAutomaticRealtimeTurn } from './proactiveReceptionVoiceLoop'
import {
  RemoteVisionClient,
  remoteVisionUrl,
  type RemoteVisionDetection,
  type RemoteVisionStatus,
  type RemoteVisitEnded,
} from './remoteVisionClient'
import {
  VisitOrchestrator,
  type VisitCandidate,
  type VisitEndContext,
} from './visitOrchestrator'
import type { VisitLease } from './visitLeaseRegistry'

const ENABLED_KEY = 'smartoffice_proximity_greeting_enabled'
const REMOTE_FALLBACK_DELAY_MS = 8_000
const PRIMARY_ABSENCE_GRACE_MS = 2_000
const PROACTIVE_LOOP_POLL_MS = 150
const PROACTIVE_SILENCE_RETRY_MS = 500
const PROACTIVE_ERROR_RETRY_MS = 1_000
const GREETING_REQUEST_TIMEOUT_MS = 5_000
const VISIT_ARCHIVE_TIMEOUT_MS = 3_000

type VisionSourceMode = 'remote' | 'remote-with-fallback' | 'mediapipe' | 'disabled'
export type ProximitySource = 'remote' | 'mediapipe' | 'disabled'
export type ProximityStatus =
  | ProximityDetectorStatus
  | RemoteVisionStatus
  | 'fallback'
  | 'disabled'

type Detection = ProximityDetection | RemoteVisionDetection

type GreetingEnvelope = {
  ok?: boolean
  triggered?: boolean
  greeting?: string
  reason?: string
  visit_id?: string | null
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortError('Operation aborted.'))
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup()
      resolve()
    }, milliseconds)
    const onAbort = () => {
      window.clearTimeout(timer)
      cleanup()
      reject(abortError('Operation aborted.'))
    }
    const cleanup = () => signal?.removeEventListener('abort', onAbort)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function greetFeatureEnabled(): boolean {
  const configured = String(import.meta.env.enable_greet ?? 'true').trim().toLowerCase()
  return !['false', '0', 'off', 'no'].includes(configured)
}

function configuredSourceMode(): VisionSourceMode {
  const configured = String(import.meta.env.VITE_VISION_SOURCE ?? 'remote')
    .trim()
    .toLowerCase()
  if (configured === 'mediapipe') return 'mediapipe'
  if (configured === 'remote-with-fallback') return 'remote-with-fallback'
  if (configured === 'disabled') return 'disabled'
  return 'remote'
}

function fallbackGreetingText(detection: Detection, language: 'zh' | 'en'): string {
  if ('greeting_kind' in detection) {
    const name = String(detection.display_name ?? '').trim()
    if (detection.greeting_kind === 'registered_identity' && name) {
      return language === 'zh' ? `欢迎回来，${name}。` : `Welcome back, ${name}.`
    }
    if (detection.greeting_kind === 'returning_anonymous') {
      return language === 'zh' ? '欢迎回来。' : 'Welcome back.'
    }
  }
  return language === 'zh' ? '欢迎来到我们的办公室。' : 'Welcome to our office.'
}

function farewellText(language: 'zh' | 'en'): string {
  return language === 'zh'
    ? '感谢您的来访，欢迎下次再来。'
    : 'Thank you for visiting. We hope to see you again soon.'
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  parentSignal?: AbortSignal,
): Promise<Response> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  const abortFromParent = () => controller.abort()
  parentSignal?.addEventListener('abort', abortFromParent, { once: true })
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    window.clearTimeout(timer)
    parentSignal?.removeEventListener('abort', abortFromParent)
  }
}

async function speakRealtimeFarewell(
  text: string,
  language: 'zh' | 'en',
  signal: AbortSignal,
): Promise<void> {
  if (signal.aborted) return
  try {
    await realtimeAgent.speakExact(text, language, signal)
  } catch (error) {
    if (signal.aborted || (error instanceof Error && error.name === 'AbortError')) return
    // Voice consistency takes priority over guaranteed farewell playback. If
    // Realtime cannot start, do not switch to a visibly different local voice.
    console.error('[ProximityDebug] realtime-farewell-speech-error', {
      text,
      language,
      message: errorText(error),
    })
  }
}

function remoteVisitId(detection: RemoteVisionDetection): string {
  return String(detection.visit_id ?? detection.visitor_session_id).trim()
}

function detectionIdentity(detection: Detection): {
  identityId: string | null
  displayName: string | null
} {
  if (!('identity_id' in detection)) return { identityId: null, displayName: null }
  return {
    identityId: String(detection.identity_id ?? '').trim() || null,
    displayName: String(detection.display_name ?? '').trim() || null,
  }
}

const GREET_FEATURE_ENABLED = greetFeatureEnabled()
const SOURCE_MODE = configuredSourceMode()

export type ProximityGreetingController = {
  enabled: boolean
  status: ProximityStatus
  source: ProximitySource
  detail: string
  endpoint: string
  lastDetection: Detection | null
  setEnabled: (enabled: boolean) => void
}

export function useProximityGreeting(
  controller: OfficeVoiceController,
): ProximityGreetingController {
  const featureAvailable = GREET_FEATURE_ENABLED && SOURCE_MODE !== 'disabled'
  const [enabled, setEnabledState] = useState(
    () => featureAvailable && localStorage.getItem(ENABLED_KEY) !== 'false',
  )
  const [status, setStatus] = useState<ProximityStatus>(enabled ? 'starting' : 'disabled')
  const [source, setSource] = useState<ProximitySource>(
    SOURCE_MODE === 'mediapipe' ? 'mediapipe' : SOURCE_MODE === 'disabled' ? 'disabled' : 'remote',
  )
  const [detail, setDetail] = useState(
    featureAvailable ? '' : 'disabled by configuration; camera and remote vision are not started',
  )
  const [lastDetection, setLastDetection] = useState<Detection | null>(null)

  const controllerRef = useRef(controller)
  const monitorRef = useRef<ProximityFaceMonitor | null>(null)
  const remoteRef = useRef<RemoteVisionClient | null>(null)
  const orchestratorRef = useRef<VisitOrchestrator<Detection> | null>(null)
  const localVisitIdRef = useRef<string | null>(null)
  const latestDetectionRef = useRef<Detection | null>(null)

  useEffect(() => {
    controllerRef.current = controller
  }, [controller])

  useEffect(() => {
    if (!featureAvailable || !enabled) {
      setStatus('disabled')
      setSource('disabled')
      setLastDetection(null)
      latestDetectionRef.current = null
      monitorRef.current?.stop()
      monitorRef.current = null
      remoteRef.current?.stop()
      remoteRef.current = null
      orchestratorRef.current?.dispose()
      orchestratorRef.current = null
      window.speechSynthesis?.cancel()
      void voiceOutputManager.stop().catch(() => undefined)
      void realtimeAgent.abortCapture().catch(() => undefined)
      return
    }

    let disposed = false
    let fallbackTimer: number | null = null

    const postGreeting = async (
      lease: VisitLease,
      candidate: VisitCandidate<Detection>,
    ): Promise<boolean> => {
      const current = controllerRef.current
      const detection = candidate.detection
      let response: Response
      try {
        response = await fetchWithTimeout(
          `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(current.conversationId)}/proximity-greeting`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json; charset=utf-8' },
            body: JSON.stringify({
              language: current.language,
              actor_type: current.actor,
              ...detection,
              visitor_session_id: lease.visitId,
              visit_id: lease.visitId,
            }),
          },
          GREETING_REQUEST_TIMEOUT_MS,
          lease.signal,
        )
      } catch (error) {
        if (!lease.signal.aborted) {
          console.error('[ProximityDebug] visit-greeting-request-error', {
            visitId: lease.visitId,
            epoch: lease.epoch,
            message: errorText(error),
          })
        }
        return false
      }
      if (!orchestratorRef.current?.isCurrent(lease)) return false
      if (!response.ok) {
        console.error('[ProximityDebug] visit-greeting-http-error', {
          visitId: lease.visitId,
          status: response.status,
          detail: await response.text().catch(() => ''),
        })
        return false
      }
      const payload = (await response.json()) as GreetingEnvelope
      if (!payload.triggered) {
        console.error('[ProximityDebug] visit-greeting-not-triggered', {
          visitId: lease.visitId,
          reason: payload.reason ?? 'unknown',
        })
        return false
      }

      const spoken = payload.greeting?.trim() || fallbackGreetingText(detection, current.language)
      window.dispatchEvent(
        new CustomEvent('smartoffice:host-intro-start', {
          detail: { detection, welcomeText: spoken, visitId: lease.visitId, epoch: lease.epoch },
        }),
      )
      try {
        await voiceOutputManager.speak(spoken, current.language)
      } catch (error) {
        if (!lease.signal.aborted) {
          console.error('[ProximityDebug] visit-greeting-speech-error', {
            visitId: lease.visitId,
            epoch: lease.epoch,
            message: errorText(error),
          })
        }
        return false
      }
      return Boolean(orchestratorRef.current?.isCurrent(lease))
    }

    const runConversation = async (
      lease: VisitLease,
      _candidate: VisitCandidate<Detection>,
    ): Promise<void> => {
      console.info('[ProximityDebug] proactive-reception-started', {
        visitorSessionId: lease.visitId,
        epoch: lease.epoch,
        lifetimeRule: 'visit-lease',
      })
      while (!disposed && orchestratorRef.current?.isCurrent(lease)) {
        const current = controllerRef.current
        const ready =
          current.panel === 'idle' &&
          !current.listening &&
          !current.runtime.outputActive &&
          !current.recordingActive &&
          !current.recordingSaving
        if (!ready) {
          try {
            await wait(PROACTIVE_LOOP_POLL_MS, lease.signal)
          } catch {
            break
          }
          continue
        }

        const result = await captureAutomaticRealtimeTurn(
          () => controllerRef.current,
          lease.signal,
        )
        if (!orchestratorRef.current?.isCurrent(lease)) break
        if (result.kind === 'heard') {
          console.info('[ProximityDebug] proactive-reception-user-turn', {
            visitorSessionId: lease.visitId,
            epoch: lease.epoch,
            transcript: result.transcript,
          })
          continue
        }
        if (result.kind === 'silence') {
          try {
            await wait(PROACTIVE_SILENCE_RETRY_MS, lease.signal)
          } catch {
            break
          }
          continue
        }
        if (result.kind === 'error') {
          console.error('[ProximityDebug] proactive-reception-turn-error', {
            visitorSessionId: lease.visitId,
            epoch: lease.epoch,
            message: result.message,
          })
          try {
            await wait(PROACTIVE_ERROR_RETRY_MS, lease.signal)
          } catch {
            break
          }
          continue
        }
        break
      }
      console.info('[ProximityDebug] proactive-reception-loop-exited', {
        visitorSessionId: lease.visitId,
        epoch: lease.epoch,
      })
    }

    const archiveVisit = async (ended: VisitEndContext<Detection>): Promise<void> => {
      const current = controllerRef.current
      const identity = detectionIdentity(ended.candidate.detection)
      for (let attempt = 1; attempt <= 2; attempt += 1) {
        try {
          const response = await fetchWithTimeout(
            `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(current.conversationId)}/visit-end`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json; charset=utf-8' },
              body: JSON.stringify({
                visitor_session_id: ended.lease.visitId,
                identity_id: identity.identityId,
                display_name: identity.displayName,
                language: current.language,
                reason: ended.reason,
              }),
            },
            VISIT_ARCHIVE_TIMEOUT_MS,
          )
          if (response.ok) {
            const payload = (await response.json()) as {
              memory_saved?: boolean
              anonymous_history_discarded?: boolean
              ended?: boolean
            }
            console.info('[ProximityDebug] visit-archive-complete', {
              visitId: ended.lease.visitId,
              epoch: ended.lease.epoch,
              attempt,
              memorySaved: Boolean(payload.memory_saved),
              anonymousHistoryDiscarded: Boolean(payload.anonymous_history_discarded),
              ended: Boolean(payload.ended),
            })
            return
          }
          console.error('[ProximityDebug] visit-archive-http-error', {
            visitId: ended.lease.visitId,
            attempt,
            status: response.status,
          })
        } catch (error) {
          console.error('[ProximityDebug] visit-archive-network-error', {
            visitId: ended.lease.visitId,
            attempt,
            message: errorText(error),
          })
        }
        await wait(250 * attempt).catch(() => undefined)
      }
    }

    const orchestrator = new VisitOrchestrator<Detection>(
      {
        onPreempt: (ended, replacement) => {
          console.info('[ProximityDebug] visit-realtime-preempted', {
            visitId: ended.lease.visitId,
            epoch: ended.lease.epoch,
            reason: ended.reason,
            replacementVisitId: replacement?.visitId ?? null,
          })
          window.dispatchEvent(new CustomEvent('smartoffice:host-intro-cancel'))
          window.speechSynthesis?.cancel()
          void realtimeAgent.abortCapture().catch(() => undefined)
          void voiceOutputManager.stop().catch(() => undefined)
          if (localVisitIdRef.current === ended.lease.visitId) localVisitIdRef.current = null
        },
        onGreeting: postGreeting,
        onConversation: runConversation,
        onFarewell: async (ended, signal) => {
          const current = controllerRef.current
          const text = farewellText(current.language)
          console.info('[ProximityDebug] proactive-reception-farewell', {
            visitorSessionId: ended.lease.visitId,
            epoch: ended.lease.epoch,
            language: current.language,
            text,
            provider: 'gpt-realtime-2',
            interruptible: true,
          })
          await speakRealtimeFarewell(text, current.language, signal)
        },
        onArchive: archiveVisit,
      },
      PRIMARY_ABSENCE_GRACE_MS,
    )
    orchestratorRef.current = orchestrator

    const localCandidate = (detection: ProximityDetection): VisitCandidate<Detection> => {
      if (!localVisitIdRef.current) {
        localVisitIdRef.current = `local-${crypto.randomUUID()}`
      }
      return {
        visitId: localVisitIdRef.current,
        detection,
        sourceEvent: 'mediapipe_primary_visible',
      }
    }

    const startMediaPipe = (fallback: boolean): void => {
      if (disposed || monitorRef.current) return
      setSource('mediapipe')
      if (fallback) {
        setStatus('fallback')
        setDetail('RTX vision is unavailable; browser MediaPipe fallback is active.')
      }
      const monitor = new ProximityFaceMonitor(
        () => true,
        async (detection) => {
          const candidate = localCandidate(detection)
          orchestrator.requestGreeting(candidate)
          return true
        },
        (nextStatus, nextDetail = '') => {
          if (!fallback || nextStatus === 'error' || nextStatus === 'blocked') {
            setStatus(nextStatus)
            setDetail(nextDetail)
          }
        },
        (detection) => {
          setLastDetection(detection)
          latestDetectionRef.current = detection
          if (detection) {
            orchestrator.observeVisible(localCandidate(detection))
          } else {
            orchestrator.markAbsent('local_primary_absent')
          }
        },
      )
      monitorRef.current = monitor
      void monitor.start()
    }

    if (SOURCE_MODE === 'mediapipe') {
      startMediaPipe(false)
    } else {
      setSource('remote')
      const remote = new RemoteVisionClient({
        url: remoteVisionUrl(),
        onStatus: (nextStatus, nextDetail) => {
          if (disposed) return
          setStatus(nextStatus)
          setDetail(nextDetail)
          if (nextStatus === 'connected') {
            if (fallbackTimer !== null) window.clearTimeout(fallbackTimer)
            fallbackTimer = null
            monitorRef.current?.stop()
            monitorRef.current = null
            setSource('remote')
          } else {
            orchestrator.markAbsent(`remote_${nextStatus}`)
            if (
              SOURCE_MODE === 'remote-with-fallback' &&
              fallbackTimer === null &&
              !monitorRef.current
            ) {
              fallbackTimer = window.setTimeout(
                () => startMediaPipe(true),
                REMOTE_FALLBACK_DELAY_MS,
              )
            }
          }
        },
        onDetection: (detection) => {
          setLastDetection(detection)
          latestDetectionRef.current = detection
          if (detection?.visible) {
            const visitId = remoteVisitId(detection)
            if (!visitId) return
            orchestrator.observeVisible({
              visitId,
              detection,
              sourceEvent: detection.source_event,
            })
          } else {
            orchestrator.markAbsent('remote_primary_absent')
          }
        },
        onGreetingCandidate: (detection) => {
          const visitId = remoteVisitId(detection)
          if (!visitId) return
          orchestrator.requestGreeting({
            visitId,
            detection,
            sourceEvent: detection.source_event,
          })
        },
        onVisitEnded: (visit: RemoteVisitEnded) => {
          orchestrator.finishFromRemote(visit.visit_id, visit.source_event || 'rtx_visit_ended')
        },
        onSessionExpired: () => undefined,
      })
      remoteRef.current = remote
      remote.start()
    }

    return () => {
      disposed = true
      if (fallbackTimer !== null) window.clearTimeout(fallbackTimer)
      monitorRef.current?.stop()
      monitorRef.current = null
      remoteRef.current?.stop()
      remoteRef.current = null
      orchestrator.dispose()
      if (orchestratorRef.current === orchestrator) orchestratorRef.current = null
      window.speechSynthesis?.cancel()
      void voiceOutputManager.stop().catch(() => undefined)
      void realtimeAgent.abortCapture().catch(() => undefined)
    }
  }, [enabled, featureAvailable])

  function setEnabled(next: boolean): void {
    if (!featureAvailable) {
      localStorage.setItem(ENABLED_KEY, 'false')
      setEnabledState(false)
      return
    }
    localStorage.setItem(ENABLED_KEY, String(next))
    setEnabledState(next)
  }

  return {
    enabled: featureAvailable && enabled,
    status,
    source,
    detail,
    endpoint: source === 'remote' ? remoteVisionUrl() : '',
    lastDetection,
    setEnabled,
  }
}
