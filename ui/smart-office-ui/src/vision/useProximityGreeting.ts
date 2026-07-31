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
} from './remoteVisionClient'

const ENABLED_KEY = 'smartoffice_proximity_greeting_enabled'
const REMOTE_FALLBACK_DELAY_MS = 8_000
const REMOTE_ATTEMPT_COOLDOWN_MS = 2_000
const PRIMARY_ABSENCE_GRACE_MS = 2_000
const PROACTIVE_LOOP_POLL_MS = 200
const PROACTIVE_SILENCE_RETRY_MS = 750
const PROACTIVE_ERROR_RETRY_MS = 1_500

type VisionSourceMode = 'remote' | 'remote-with-fallback' | 'mediapipe' | 'disabled'
export type ProximitySource = 'remote' | 'mediapipe' | 'disabled'
export type ProximityStatus =
  | ProximityDetectorStatus
  | RemoteVisionStatus
  | 'fallback'
  | 'disabled'

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
  return language === 'zh' ? '欢迎下次再来。' : 'Welcome to visit again next time.'
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
  const primaryBoxVisibleRef = useRef(false)
  const primaryAbsenceTimerRef = useRef<number | null>(null)
  const pendingSessionsRef = useRef(new Set<string>())
  const lastAttemptAtRef = useRef(new Map<string, number>())
  const localGreetingInFlightRef = useRef(false)
  const visitorSessionActiveRef = useRef(false)
  const proactiveSessionRef = useRef<string | null>(null)
  const proactiveLoopGenerationRef = useRef(0)
  const proactiveTurnAbortRef = useRef<AbortController | null>(null)

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

    const markStandby = async (reason: string): Promise<void> => {
      const current = controllerRef.current
      try {
        await fetch(
          `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(current.conversationId)}/standby`,
          { method: 'POST' },
        )
      } catch {
        // Visitor-session rearm must not depend on this best-effort Backend sync.
      }
      console.info('[ProximityDebug] proactive-reception-standby', {
        reason,
        visitorSessionId: proactiveSessionRef.current,
      })
    }

    const stopProactiveReception = async (
      reason: string,
      restoreStandby: boolean,
      speakFarewell: boolean,
    ): Promise<void> => {
      proactiveLoopGenerationRef.current += 1
      proactiveTurnAbortRef.current?.abort()
      proactiveTurnAbortRef.current = null
      const stoppedSession = proactiveSessionRef.current
      proactiveSessionRef.current = null
      visitorSessionActiveRef.current = false

      await realtimeAgent.abortCapture().catch(() => undefined)
      await controllerRef.current.stopSpeaking().catch(() => undefined)

      if (speakFarewell) {
        const current = controllerRef.current
        const farewell = farewellText(current.language)
        console.info('[ProximityDebug] proactive-reception-farewell', {
          visitorSessionId: stoppedSession,
          language: current.language,
          text: farewell,
        })
        await voiceOutputManager.speak(farewell, current.language).catch((error) => {
          console.error('[ProximityDebug] proactive-reception-farewell-error', {
            visitorSessionId: stoppedSession,
            message: error instanceof Error ? error.message : String(error),
          })
        })
      }

      if (restoreStandby) await markStandby(reason)
      console.info('[ProximityDebug] proactive-reception-stopped', {
        reason,
        visitorSessionId: stoppedSession,
      })
    }

    const confirmPrimaryAbsent = (reason: string): void => {
      primaryAbsenceTimerRef.current = null
      if (primaryBoxVisibleRef.current) return

      const absentSession = activeRemoteSessionRef.current
      if (absentSession && greetedVisitRef.current === absentSession) {
        greetedVisitRef.current = null
      }
      activeRemoteSessionRef.current = null
      pendingSessionsRef.current.clear()

      if (proactiveSessionRef.current) {
        void stopProactiveReception(`${reason}_confirmed`, true, true)
      } else {
        visitorSessionActiveRef.current = false
        void markStandby(`${reason}_confirmed`)
      }
    }

    const schedulePrimaryAbsence = (reason: string): void => {
      if (primaryAbsenceTimerRef.current !== null) return
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

    const runProactiveReception = async (visitorSessionId: string): Promise<void> => {
      proactiveLoopGenerationRef.current += 1
      const generation = proactiveLoopGenerationRef.current
      proactiveSessionRef.current = visitorSessionId
      visitorSessionActiveRef.current = true
      console.info('[ProximityDebug] proactive-reception-started', {
        visitorSessionId,
        lifetimeRule: 'primary-box-visible-or-absence-under-2s',
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

    const eligibleNow = (): boolean => {
      const current = controllerRef.current
      const eligibility = {
        conversationPhase: current.conversationPhase,
        panel: current.panel,
        active: current.active,
        listening: current.listening,
        outputActive: current.runtime.outputActive,
        recordingActive: current.recordingActive,
        recordingSaving: current.recordingSaving,
      }
      const phaseAllowsGreeting = eligibility.conversationPhase === 'standby'
      const eligible =
        phaseAllowsGreeting &&
        eligibility.panel === 'idle' &&
        !eligibility.active &&
        !eligibility.listening &&
        !eligibility.outputActive &&
        !eligibility.recordingActive &&
        !eligibility.recordingSaving
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

    const attemptGreeting = async (
      detection: ProximityDetection | RemoteVisionDetection,
      visitorSessionId = '',
    ): Promise<boolean> => {
      if (!eligibleNow()) return false
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
          void runProactiveReception(proactiveSessionId)
        } else {
          window.dispatchEvent(new CustomEvent('smartoffice:host-intro-cancel'))
        }
        return triggered
      } finally {
        if (visitorSessionId) pendingSessionsRef.current.delete(visitorSessionId)
        else localGreetingInFlightRef.current = false
      }
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
          if (detection) {
            primaryBoxVisibleRef.current = true
            visitorSessionActiveRef.current = true
            clearPrimaryAbsenceTimer()
            if (detection.visitor_session_id) {
              activeRemoteSessionRef.current = detection.visitor_session_id
            }
            return
          }

          primaryBoxVisibleRef.current = false
          schedulePrimaryAbsence('remote_primary_box_absent')
        },
        onGreetingCandidate: (detection: RemoteVisionDetection) => {
          void attemptGreeting(detection, detection.visitor_session_id)
        },
        onSessionExpired: (visitorSessionId) => {
          pendingSessionsRef.current.delete(visitorSessionId)
          lastAttemptAtRef.current.delete(visitorSessionId)
          if (
            !primaryBoxVisibleRef.current &&
            (activeRemoteSessionRef.current === visitorSessionId ||
              proactiveSessionRef.current === visitorSessionId)
          ) {
            schedulePrimaryAbsence('remote_session_expired')
          }
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
      visitorSessionActiveRef.current = false
      primaryBoxVisibleRef.current = false
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
