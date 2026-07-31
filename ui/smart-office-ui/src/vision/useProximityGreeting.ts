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

const ENABLED_KEY = 'smartoffice_proximity_greeting_enabled'
const REMOTE_FALLBACK_DELAY_MS = 8_000
const REMOTE_ATTEMPT_COOLDOWN_MS = 2_000
const PRIMARY_ABSENCE_GRACE_MS = 2_000
const PROACTIVE_LOOP_POLL_MS = 200
const PROACTIVE_SILENCE_RETRY_MS = 750
const PROACTIVE_ERROR_RETRY_MS = 1_500
const NEXT_VISITOR_REARM_TIMEOUT_MS = 7_000
const VISIT_END_ATTEMPTS = 3

type VisionSourceMode = 'remote' | 'remote-with-fallback' | 'mediapipe' | 'disabled'
export type ProximitySource = 'remote' | 'mediapipe' | 'disabled'
export type ProximityStatus =
  | ProximityDetectorStatus
  | RemoteVisionStatus
  | 'fallback'
  | 'disabled'

type GreetingCandidate = {
  detection: ProximityDetection | RemoteVisionDetection
  visitorSessionId: string
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
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

function greetingText(
  detection: ProximityDetection | RemoteVisionDetection,
  language: 'zh' | 'en',
): string {
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

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

async function browserFarewellFallback(text: string, language: 'zh' | 'en'): Promise<void> {
  if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') {
    throw new Error('Browser speech synthesis is unavailable.')
  }
  await new Promise<void>((resolve, reject) => {
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = language === 'zh' ? 'zh-CN' : 'en-AU'
    utterance.rate = language === 'zh' ? 0.94 : 1
    const timeout = window.setTimeout(() => {
      window.speechSynthesis.cancel()
      reject(new Error('Browser farewell speech timed out.'))
    }, 12_000)
    utterance.onend = () => {
      window.clearTimeout(timeout)
      resolve()
    }
    utterance.onerror = (event) => {
      window.clearTimeout(timeout)
      reject(new Error(`Browser farewell speech failed: ${event.error}`))
    }
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
  })
}

const GREET_FEATURE_ENABLED = greetFeatureEnabled()
const SOURCE_MODE = configuredSourceMode()

export type ProximityGreetingController = {
  enabled: boolean
  status: ProximityStatus
  source: ProximitySource
  detail: string
  endpoint: string
  lastDetection: ProximityDetection | RemoteVisionDetection | null
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
  const [lastDetection, setLastDetection] = useState<
    ProximityDetection | RemoteVisionDetection | null
  >(null)

  const controllerRef = useRef(controller)
  const previousConversationPhaseRef = useRef(controller.conversationPhase)
  const lastEligibilitySignatureRef = useRef('')
  const monitorRef = useRef<ProximityFaceMonitor | null>(null)
  const remoteRef = useRef<RemoteVisionClient | null>(null)
  const greetedVisitRef = useRef<string | null>(null)
  const activeRemoteSessionRef = useRef<string | null>(null)
  const activeDetectionRef = useRef<ProximityDetection | RemoteVisionDetection | null>(null)
  const primaryBoxVisibleRef = useRef(false)
  const primaryAbsenceTimerRef = useRef<number | null>(null)
  const pendingSessionsRef = useRef(new Set<string>())
  const lastAttemptAtRef = useRef(new Map<string, number>())
  const localGreetingInFlightRef = useRef(false)
  const visitorSessionActiveRef = useRef(false)
  const proactiveSessionRef = useRef<string | null>(null)
  const proactiveLoopGenerationRef = useRef(0)
  const proactiveTurnAbortRef = useRef<AbortController | null>(null)
  const proactiveLoopPromiseRef = useRef<Promise<void> | null>(null)
  const visitClosingPromiseRef = useRef<Promise<void> | null>(null)
  const queuedGreetingRef = useRef<GreetingCandidate | null>(null)
  const lastVisitEndSequenceRef = useRef(0)

  useEffect(() => {
    controllerRef.current = controller
  }, [controller])

  useEffect(() => {
    if (!featureAvailable) return
    const previousPhase = previousConversationPhaseRef.current
    const nextPhase = controller.conversationPhase
    previousConversationPhaseRef.current = nextPhase
    if (previousPhase === 'standby' && nextPhase !== 'standby') {
      monitorRef.current?.suppressUntilAbsent()
    }
  }, [controller.conversationPhase, featureAvailable])

  useEffect(() => {
    if (!featureAvailable || !enabled) {
      setStatus('disabled')
      setSource('disabled')
      setDetail(
        featureAvailable ? '' : 'disabled by configuration; camera and remote vision are not started',
      )
      setLastDetection(null)
      activeDetectionRef.current = null
      monitorRef.current?.stop()
      monitorRef.current = null
      remoteRef.current?.stop()
      remoteRef.current = null
      if (primaryAbsenceTimerRef.current !== null) {
        window.clearTimeout(primaryAbsenceTimerRef.current)
        primaryAbsenceTimerRef.current = null
      }
      proactiveLoopGenerationRef.current += 1
      proactiveTurnAbortRef.current?.abort()
      proactiveTurnAbortRef.current = null
      proactiveSessionRef.current = null
      proactiveLoopPromiseRef.current = null
      visitClosingPromiseRef.current = null
      queuedGreetingRef.current = null
      visitorSessionActiveRef.current = false
      primaryBoxVisibleRef.current = false
      void realtimeAgent.abortCapture().catch(() => undefined)
      greetedVisitRef.current = null
      activeRemoteSessionRef.current = null
      return
    }

    let disposed = false
    let fallbackTimer: number | null = null

    const clearPrimaryAbsenceTimer = (): void => {
      if (primaryAbsenceTimerRef.current === null) return
      window.clearTimeout(primaryAbsenceTimerRef.current)
      primaryAbsenceTimerRef.current = null
      console.info('[ProximityDebug] primary-box-absence-cancelled', {
        visitorSessionId: activeRemoteSessionRef.current ?? proactiveSessionRef.current,
      })
    }

    const eligibleNow = (): boolean => {
      const current = controllerRef.current
      const eligibility = {
        conversationPhase: current.conversationPhase,
        panel: current.panel,
        active: current.active,
        listening: current.listening,
        outputActive: current.runtime.outputActive,
        microphoneAttached: current.runtime.microphoneAttached,
        recordingActive: current.recordingActive,
        recordingSaving: current.recordingSaving,
        visitClosing: visitClosingPromiseRef.current !== null,
      }
      const phaseAllowsGreeting = eligibility.conversationPhase === 'standby'
      const eligible =
        phaseAllowsGreeting &&
        eligibility.panel === 'idle' &&
        !eligibility.active &&
        !eligibility.listening &&
        !eligibility.outputActive &&
        !eligibility.microphoneAttached &&
        !eligibility.recordingActive &&
        !eligibility.recordingSaving &&
        !eligibility.visitClosing
      const signature = JSON.stringify({ ...eligibility, phaseAllowsGreeting, eligible })
      if (signature !== lastEligibilitySignatureRef.current) {
        lastEligibilitySignatureRef.current = signature
        console.info('[ProximityDebug] frontend-eligibility', {
          ...eligibility,
          phaseAllowsGreeting,
          eligible,
          source: SOURCE_MODE,
        })
      }
      return eligible
    }

    const postVisitEnd = async (
      visitId: string,
      detection: ProximityDetection | RemoteVisionDetection | null,
      reason: string,
    ): Promise<boolean> => {
      const current = controllerRef.current
      const identityId =
        detection && 'identity_id' in detection ? detection.identity_id ?? null : null
      const displayName =
        detection && 'display_name' in detection ? detection.display_name ?? null : null
      for (let attempt = 1; attempt <= VISIT_END_ATTEMPTS; attempt += 1) {
        try {
          const response = await fetch(
            `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(current.conversationId)}/visit-end`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json; charset=utf-8' },
              body: JSON.stringify({
                visitor_session_id: visitId || null,
                identity_id: identityId,
                display_name: displayName,
                language: current.language,
                reason,
              }),
            },
          )
          if (response.ok) {
            const payload = (await response.json()) as {
              memory_saved?: boolean
              anonymous_history_discarded?: boolean
              ended?: boolean
            }
            console.info('[ProximityDebug] visit-end-synchronized', {
              visitorSessionId: visitId,
              attempt,
              memorySaved: Boolean(payload.memory_saved),
              anonymousHistoryDiscarded: Boolean(payload.anonymous_history_discarded),
              ended: Boolean(payload.ended),
            })
            return true
          }
          console.error('[ProximityDebug] visit-end-http-error', {
            visitorSessionId: visitId,
            attempt,
            status: response.status,
            detail: await response.text(),
          })
        } catch (error) {
          console.error('[ProximityDebug] visit-end-network-error', {
            visitorSessionId: visitId,
            attempt,
            message: errorText(error),
          })
        }
        await wait(250 * attempt)
      }
      return false
    }

    const speakFarewellReliably = async (
      text: string,
      language: 'zh' | 'en',
      visitorSessionId: string,
    ): Promise<void> => {
      let realtimeError = ''
      if (voiceOutputManager.selectedProvider() !== 'none') {
        try {
          await voiceOutputManager.speak(text, language)
          return
        } catch (error) {
          realtimeError = errorText(error)
        }
      }
      console.warn('[ProximityDebug] proactive-reception-farewell-fallback', {
        visitorSessionId,
        realtimeError: realtimeError || 'Realtime voice output is disabled.',
      })
      try {
        await browserFarewellFallback(text, language)
      } catch (error) {
        console.error('[ProximityDebug] proactive-reception-farewell-error', {
          visitorSessionId,
          realtimeError,
          fallbackError: errorText(error),
        })
      }
    }

    const runProactiveReception = async (visitorSessionId: string): Promise<void> => {
      proactiveLoopGenerationRef.current += 1
      const generation = proactiveLoopGenerationRef.current
      proactiveSessionRef.current = visitorSessionId
      visitorSessionActiveRef.current = true
      console.info('[ProximityDebug] proactive-reception-started', {
        visitorSessionId,
        lifetimeRule: 'visible-primary-visit',
      })

      while (
        !disposed &&
        generation === proactiveLoopGenerationRef.current &&
        proactiveSessionRef.current === visitorSessionId &&
        visitorSessionActiveRef.current
      ) {
        const current = controllerRef.current
        const readyForUser =
          current.conversationPhase === 'awaiting_user' &&
          current.panel === 'idle' &&
          !current.active &&
          !current.listening &&
          !current.runtime.outputActive &&
          !current.recordingActive &&
          !current.recordingSaving

        if (!readyForUser) {
          await wait(PROACTIVE_LOOP_POLL_MS)
          continue
        }

        const turnAbort = new AbortController()
        proactiveTurnAbortRef.current = turnAbort
        const result = await captureAutomaticRealtimeTurn(
          () => controllerRef.current,
          turnAbort.signal,
        )
        if (proactiveTurnAbortRef.current === turnAbort) {
          proactiveTurnAbortRef.current = null
        }

        if (
          disposed ||
          generation !== proactiveLoopGenerationRef.current ||
          proactiveSessionRef.current !== visitorSessionId ||
          !visitorSessionActiveRef.current
        ) {
          break
        }

        if (result.kind === 'heard') {
          console.info('[ProximityDebug] proactive-reception-user-turn', {
            visitorSessionId,
            transcript: result.transcript,
          })
          await wait(PROACTIVE_LOOP_POLL_MS)
          continue
        }

        if (result.kind === 'silence') {
          console.info('[ProximityDebug] proactive-reception-silence', {
            visitorSessionId,
          })
          await wait(PROACTIVE_SILENCE_RETRY_MS)
          continue
        }

        if (result.kind === 'error') {
          console.error('[ProximityDebug] proactive-reception-turn-error', {
            visitorSessionId,
            message: result.message,
          })
          await wait(PROACTIVE_ERROR_RETRY_MS)
          continue
        }

        break
      }
    }

    const startProactiveReception = (visitorSessionId: string): void => {
      const loopPromise = runProactiveReception(visitorSessionId)
      proactiveLoopPromiseRef.current = loopPromise
      void loopPromise.finally(() => {
        if (proactiveLoopPromiseRef.current === loopPromise) {
          proactiveLoopPromiseRef.current = null
        }
      })
    }

    const attemptGreeting = async (
      detection: ProximityDetection | RemoteVisionDetection,
      visitorSessionId = '',
    ): Promise<boolean> => {
      const candidate: GreetingCandidate = {
        detection,
        visitorSessionId: visitorSessionId || '__local__',
      }
      if (visitClosingPromiseRef.current) {
        queuedGreetingRef.current = candidate
        console.info('[ProximityDebug] next-visitor-greeting-queued', {
          visitorSessionId: candidate.visitorSessionId,
          reason: 'visit_closing',
        })
        return false
      }
      if (!eligibleNow()) {
        if (!proactiveSessionRef.current) queuedGreetingRef.current = candidate
        return false
      }
      if (visitorSessionId && greetedVisitRef.current === visitorSessionId) return false
      const attemptKey = visitorSessionId || '__local__'
      const now = Date.now()
      if (now - (lastAttemptAtRef.current.get(attemptKey) ?? 0) < REMOTE_ATTEMPT_COOLDOWN_MS) {
        return false
      }
      if (visitorSessionId) {
        if (pendingSessionsRef.current.has(visitorSessionId)) return false
        pendingSessionsRef.current.add(visitorSessionId)
      } else if (localGreetingInFlightRef.current) {
        return false
      } else {
        localGreetingInFlightRef.current = true
      }

      lastAttemptAtRef.current.set(attemptKey, now)
      primaryBoxVisibleRef.current = true
      visitorSessionActiveRef.current = true
      activeDetectionRef.current = detection
      clearPrimaryAbsenceTimer()
      const welcomeText = greetingText(detection, controllerRef.current.language)
      window.dispatchEvent(
        new CustomEvent('smartoffice:host-intro-start', {
          detail: { detection, welcomeText },
        }),
      )

      try {
        const triggered = await controllerRef.current.triggerProximityGreeting(detection)
        if (triggered) {
          const proactiveSessionId = visitorSessionId || '__local__'
          if (visitorSessionId) greetedVisitRef.current = visitorSessionId
          queuedGreetingRef.current = null
          startProactiveReception(proactiveSessionId)
        } else {
          window.dispatchEvent(new CustomEvent('smartoffice:host-intro-cancel'))
          if (!proactiveSessionRef.current) queuedGreetingRef.current = candidate
        }
        return triggered
      } finally {
        if (visitorSessionId) pendingSessionsRef.current.delete(visitorSessionId)
        else localGreetingInFlightRef.current = false
      }
    }

    const drainQueuedGreeting = async (): Promise<void> => {
      const deadline = performance.now() + NEXT_VISITOR_REARM_TIMEOUT_MS
      while (!disposed && performance.now() < deadline) {
        if (visitClosingPromiseRef.current) {
          await wait(PROACTIVE_LOOP_POLL_MS)
          continue
        }
        const candidate = queuedGreetingRef.current
        if (!candidate) return
        if (
          candidate.visitorSessionId !== '__local__' &&
          activeRemoteSessionRef.current !== candidate.visitorSessionId
        ) {
          queuedGreetingRef.current = null
          return
        }
        if (!primaryBoxVisibleRef.current) return
        if (!eligibleNow()) {
          await wait(PROACTIVE_LOOP_POLL_MS)
          continue
        }
        queuedGreetingRef.current = null
        const triggered = await attemptGreeting(
          candidate.detection,
          candidate.visitorSessionId === '__local__' ? '' : candidate.visitorSessionId,
        )
        if (triggered) {
          console.info('[ProximityDebug] next-visitor-greeting-started', {
            visitorSessionId: candidate.visitorSessionId,
          })
          return
        }
        if (!queuedGreetingRef.current) queuedGreetingRef.current = candidate
        await wait(PROACTIVE_LOOP_POLL_MS)
      }
      console.error('[ProximityDebug] next-visitor-rearm-timeout', {
        visitorSessionId: queuedGreetingRef.current?.visitorSessionId ?? null,
        conversationPhase: controllerRef.current.conversationPhase,
        panel: controllerRef.current.panel,
      })
    }

    const closeVisit = (
      reason: string,
      visitEvent: RemoteVisitEnded | null = null,
    ): Promise<void> => {
      if (visitClosingPromiseRef.current) return visitClosingPromiseRef.current

      const closingPromise = (async () => {
        clearPrimaryAbsenceTimer()
        const stoppedSession =
          proactiveSessionRef.current ??
          activeRemoteSessionRef.current ??
          visitEvent?.visit_id ??
          '__local__'
        const detection = activeDetectionRef.current
        console.info('[ProximityDebug] visit-closing-started', {
          visitorSessionId: stoppedSession,
          reason,
          sourceEvent: visitEvent?.source_event ?? 'local_absence_fallback',
        })

        proactiveLoopGenerationRef.current += 1
        visitorSessionActiveRef.current = false
        proactiveTurnAbortRef.current?.abort()
        proactiveTurnAbortRef.current = null

        const loopPromise = proactiveLoopPromiseRef.current
        if (loopPromise) {
          await loopPromise.catch((error) => {
            console.error('[ProximityDebug] proactive-loop-close-error', {
              visitorSessionId: stoppedSession,
              message: errorText(error),
            })
          })
        }
        proactiveLoopPromiseRef.current = null

        await realtimeAgent.abortCapture().catch(() => undefined)
        await controllerRef.current.stopSpeaking().catch(() => undefined)

        const current = controllerRef.current
        const farewell = farewellText(current.language)
        console.info('[ProximityDebug] proactive-reception-farewell', {
          visitorSessionId: stoppedSession,
          language: current.language,
          text: farewell,
        })
        await speakFarewellReliably(farewell, current.language, stoppedSession)

        const synchronized = await postVisitEnd(stoppedSession, detection, reason)
        if (!synchronized) {
          console.error('[ProximityDebug] visit-end-sync-exhausted', {
            visitorSessionId: stoppedSession,
            reason,
          })
        }

        if (greetedVisitRef.current === stoppedSession) greetedVisitRef.current = null
        if (activeRemoteSessionRef.current === stoppedSession) {
          activeRemoteSessionRef.current = null
        }
        pendingSessionsRef.current.delete(stoppedSession)
        lastAttemptAtRef.current.delete(stoppedSession)
        proactiveSessionRef.current = null
        activeDetectionRef.current = null
        console.info('[ProximityDebug] proactive-reception-stopped', {
          reason,
          visitorSessionId: stoppedSession,
          backendSynchronized: synchronized,
        })
      })()

      visitClosingPromiseRef.current = closingPromise
      void closingPromise.finally(() => {
        if (visitClosingPromiseRef.current === closingPromise) {
          visitClosingPromiseRef.current = null
        }
        if (!disposed && queuedGreetingRef.current) void drainQueuedGreeting()
      })
      return closingPromise
    }

    const confirmPrimaryAbsent = (reason: string): void => {
      primaryAbsenceTimerRef.current = null
      if (primaryBoxVisibleRef.current) return
      const sessionId = activeRemoteSessionRef.current ?? proactiveSessionRef.current
      if (!sessionId) return
      void closeVisit(`${reason}_confirmed`)
    }

    const schedulePrimaryAbsence = (reason: string): void => {
      if (primaryAbsenceTimerRef.current !== null || visitClosingPromiseRef.current) return
      console.info('[ProximityDebug] primary-box-absence-grace-started', {
        reason,
        visitorSessionId: activeRemoteSessionRef.current ?? proactiveSessionRef.current,
        graceMs: PRIMARY_ABSENCE_GRACE_MS,
      })
      primaryAbsenceTimerRef.current = window.setTimeout(
        () => confirmPrimaryAbsent(reason),
        PRIMARY_ABSENCE_GRACE_MS,
      )
    }

    const handleVisitEnded = (visit: RemoteVisitEnded): void => {
      if (visit.sequence > 0 && visit.sequence <= lastVisitEndSequenceRef.current) return
      if (visit.sequence > 0) lastVisitEndSequenceRef.current = visit.sequence
      const currentSession = proactiveSessionRef.current ?? activeRemoteSessionRef.current
      if (currentSession && visit.visit_id !== currentSession) {
        console.info('[ProximityDebug] stale-visit-end-ignored', {
          endedVisitId: visit.visit_id,
          currentVisitId: currentSession,
          sequence: visit.sequence,
        })
        return
      }
      primaryBoxVisibleRef.current = false
      void closeVisit('rtx_visit_ended', visit)
    }

    const startMediaPipe = (fallback: boolean): void => {
      if (disposed || monitorRef.current) return
      setSource('mediapipe')
      if (fallback) {
        setStatus('fallback')
        setDetail('RTX vision is unavailable; using the explicitly enabled browser MediaPipe fallback.')
      }
      const monitor = new ProximityFaceMonitor(
        eligibleNow,
        async (detection) => attemptGreeting(detection),
        (nextStatus, nextDetail = '') => {
          if (!fallback || nextStatus === 'error' || nextStatus === 'blocked') {
            setStatus(nextStatus)
            setDetail(nextDetail)
          }
        },
        (detection) => {
          setLastDetection(detection)
          activeDetectionRef.current = detection
          if (detection) {
            primaryBoxVisibleRef.current = true
            visitorSessionActiveRef.current = true
            clearPrimaryAbsenceTimer()
            return
          }
          primaryBoxVisibleRef.current = false
          schedulePrimaryAbsence('local_primary_box_absent')
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
          } else if (
            SOURCE_MODE === 'remote-with-fallback' &&
            fallbackTimer === null &&
            !monitorRef.current
          ) {
            fallbackTimer = window.setTimeout(() => startMediaPipe(true), REMOTE_FALLBACK_DELAY_MS)
          }
        },
        onDetection: (detection) => {
          setLastDetection(detection)
          activeDetectionRef.current = detection
          if (detection?.visible) {
            primaryBoxVisibleRef.current = true
            visitorSessionActiveRef.current = true
            clearPrimaryAbsenceTimer()
            if (detection.visitor_session_id) {
              const previousSession = activeRemoteSessionRef.current
              activeRemoteSessionRef.current = detection.visitor_session_id
              if (
                previousSession &&
                previousSession !== detection.visitor_session_id &&
                visitClosingPromiseRef.current
              ) {
                queuedGreetingRef.current = {
                  detection,
                  visitorSessionId: detection.visitor_session_id,
                }
              }
            }
            return
          }

          primaryBoxVisibleRef.current = false
          schedulePrimaryAbsence('remote_primary_box_absent')
        },
        onGreetingCandidate: (detection: RemoteVisionDetection) => {
          if (visitClosingPromiseRef.current) {
            queuedGreetingRef.current = {
              detection,
              visitorSessionId: detection.visitor_session_id,
            }
            return
          }
          void attemptGreeting(detection, detection.visitor_session_id)
        },
        onVisitEnded: handleVisitEnded,
        onSessionExpired: (visitorSessionId) => {
          pendingSessionsRef.current.delete(visitorSessionId)
          lastAttemptAtRef.current.delete(visitorSessionId)
        },
      })
      remoteRef.current = remote
      remote.start()
    }

    return () => {
      disposed = true
      proactiveLoopGenerationRef.current += 1
      proactiveTurnAbortRef.current?.abort()
      proactiveTurnAbortRef.current = null
      proactiveSessionRef.current = null
      proactiveLoopPromiseRef.current = null
      visitClosingPromiseRef.current = null
      queuedGreetingRef.current = null
      visitorSessionActiveRef.current = false
      primaryBoxVisibleRef.current = false
      activeDetectionRef.current = null
      void realtimeAgent.abortCapture().catch(() => undefined)
      if (fallbackTimer !== null) window.clearTimeout(fallbackTimer)
      clearPrimaryAbsenceTimer()
      monitorRef.current?.stop()
      monitorRef.current = null
      remoteRef.current?.stop()
      remoteRef.current = null
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
