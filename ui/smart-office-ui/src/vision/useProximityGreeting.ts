import { useEffect, useRef, useState } from 'react'
import {
  OFFICE_API_BASE,
  type OfficeVoiceController,
} from '../voice/useOfficeVoiceController'
import { realtimeAgent } from '../voice/realtimeAgentRuntime'
import {
  ProximityFaceMonitor,
  type ProximityDetection,
  type ProximityDetectorStatus,
} from './proximityFaceMonitor'
import {
  ProactiveListeningGate,
  type ProactiveListeningGateConfig,
  type ProactiveListeningGateSnapshot,
} from './proactiveListeningGate'
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
const PROACTIVE_LOOP_POLL_MS = 200
const PROACTIVE_SILENCE_RETRY_MS = 750
const PROACTIVE_ERROR_RETRY_MS = 1_500
const DEFAULT_SESSION_ABSENCE_SECONDS = 5
const DEFAULT_LISTEN_ENTER_BODY_RATIO = 0.11
const DEFAULT_LISTEN_EXIT_BODY_RATIO = 0.075
const DEFAULT_LISTEN_MIN_FACE_CONFIDENCE = 0.2
const DEFAULT_LISTEN_ENTER_STABLE_MS = 600
const DEFAULT_LISTEN_EXIT_GRACE_MS = 1_200

type VisionSourceMode = 'remote' | 'remote-with-fallback' | 'mediapipe' | 'disabled'
export type ProximitySource = 'remote' | 'mediapipe' | 'disabled'
export type ProximityStatus =
  | ProximityDetectorStatus
  | RemoteVisionStatus
  | 'fallback'
  | 'disabled'

type ListeningPolicy = {
  gateConfig: ProactiveListeningGateConfig
  minimumFaceConfidence: number
  sessionAbsenceMs: number
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function envNumber(name: string, fallback: number): number {
  const env = import.meta.env as unknown as Record<string, unknown>
  const value = Number(env[name])
  return Number.isFinite(value) ? value : fallback
}

function clamp(value: number): number {
  return Math.max(0, Math.min(1, value))
}

function configuredListeningPolicy(): ListeningPolicy {
  const enterBodyRatio = clamp(
    envNumber('VITE_PROACTIVE_LISTEN_ENTER_BODY_RATIO', DEFAULT_LISTEN_ENTER_BODY_RATIO),
  )
  const exitBodyRatio = clamp(
    envNumber('VITE_PROACTIVE_LISTEN_EXIT_BODY_RATIO', DEFAULT_LISTEN_EXIT_BODY_RATIO),
  )
  return {
    gateConfig: {
      enterBodyRatio,
      exitBodyRatio: Math.min(enterBodyRatio, exitBodyRatio),
      enterStableMs: Math.max(
        0,
        envNumber('VITE_PROACTIVE_LISTEN_ENTER_STABLE_MS', DEFAULT_LISTEN_ENTER_STABLE_MS),
      ),
      exitGraceMs: Math.max(
        0,
        envNumber('VITE_PROACTIVE_LISTEN_EXIT_GRACE_MS', DEFAULT_LISTEN_EXIT_GRACE_MS),
      ),
    },
    minimumFaceConfidence: clamp(
      envNumber(
        'VITE_PROACTIVE_LISTEN_MIN_FACE_CONFIDENCE',
        DEFAULT_LISTEN_MIN_FACE_CONFIDENCE,
      ),
    ),
    sessionAbsenceMs:
      Math.max(
        1,
        envNumber(
          'VITE_PROACTIVE_SESSION_ABSENCE_SECONDS',
          DEFAULT_SESSION_ABSENCE_SECONDS,
        ),
      ) * 1_000,
  }
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

function isDecline(text: string): boolean {
  const clean = text
    .trim()
    .toLocaleLowerCase()
    .replace(/[。！？!?，,；;:：]+$/g, '')
  if (!clean) return false
  return (
    /^(不用|不需要|不了|不要|不想|暂时不用|先不用|没兴趣|不体验|拒绝|算了|谢谢(?:了)?(?:，|,|\s)*(?:不用|不了)?)$/.test(
      clean,
    ) ||
    /^(no|no thanks|not now|maybe later|i(?:'m| am) not interested|i(?:'d| would) rather not|do not|don't|decline)$/.test(
      clean,
    )
  )
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
  const visitorAbsenceTimerRef = useRef<number | null>(null)
  const pendingSessionsRef = useRef(new Set<string>())
  const lastAttemptAtRef = useRef(new Map<string, number>())
  const localGreetingInFlightRef = useRef(false)
  const visitorPresentRef = useRef(false)
  const latestPrimaryDetectionRef = useRef<
    ProximityDetection | RemoteVisionDetection | null
  >(null)
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
      latestPrimaryDetectionRef.current = null
      monitorRef.current?.stop()
      monitorRef.current = null
      remoteRef.current?.stop()
      remoteRef.current = null
      if (visitorAbsenceTimerRef.current !== null) {
        window.clearTimeout(visitorAbsenceTimerRef.current)
        visitorAbsenceTimerRef.current = null
      }
      proactiveLoopGenerationRef.current += 1
      proactiveTurnAbortRef.current?.abort()
      proactiveTurnAbortRef.current = null
      proactiveSessionRef.current = null
      visitorPresentRef.current = false
      void realtimeAgent.abortCapture().catch(() => undefined)
      greetedVisitRef.current = null
      activeRemoteSessionRef.current = null
      return
    }

    let disposed = false
    let fallbackTimer: number | null = null
    const listeningPolicy = configuredListeningPolicy()
    const listeningGate = new ProactiveListeningGate(listeningPolicy.gateConfig)
    let lastListeningGateSignature = ''

    const evaluateListeningGate = (): ProactiveListeningGateSnapshot => {
      const detection = latestPrimaryDetectionRef.current
      const faceVisible = Boolean(
        detection?.face_inside_body &&
          Number(detection.face_confidence) >= listeningPolicy.minimumFaceConfidence,
      )
      const snapshot = listeningGate.update({
        primaryPresent: visitorPresentRef.current,
        faceVisible,
        bodyAreaRatio: Number(detection?.body_area_ratio) || 0,
      })
      const signature = `${snapshot.eligible}:${snapshot.reason}`
      if (signature !== lastListeningGateSignature) {
        lastListeningGateSignature = signature
        console.info('[ProximityDebug] proactive-listening-gate', {
          visitorSessionId: proactiveSessionRef.current,
          eligible: snapshot.eligible,
          reason: snapshot.reason,
          primaryPresent: snapshot.primaryPresent,
          faceVisible: snapshot.faceVisible,
          faceConfidence: Number(detection?.face_confidence) || 0,
          minimumFaceConfidence: listeningPolicy.minimumFaceConfidence,
          bodyAreaRatio: snapshot.bodyAreaRatio,
          enterBodyRatio: snapshot.enterBodyRatio,
          exitBodyRatio: snapshot.exitBodyRatio,
          transitionAgeMs: Math.round(snapshot.transitionAgeMs),
        })
      }
      return snapshot
    }

    const clearVisitorAbsenceTimer = (): void => {
      if (visitorAbsenceTimerRef.current === null) return
      window.clearTimeout(visitorAbsenceTimerRef.current)
      visitorAbsenceTimerRef.current = null
    }

    const markStandby = async (reason: string): Promise<void> => {
      const current = controllerRef.current
      try {
        await fetch(
          `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(current.conversationId)}/standby`,
          { method: 'POST' },
        )
      } catch {
        // Local UI state and visitor-session rearm must not depend on this best-effort sync.
      }
      console.info('[ProximityDebug] proactive-reception-standby', {
        reason,
        visitorSessionId: proactiveSessionRef.current,
      })
    }

    const stopProactiveReception = async (
      reason: string,
      restoreStandby: boolean,
    ): Promise<void> => {
      proactiveLoopGenerationRef.current += 1
      proactiveTurnAbortRef.current?.abort()
      proactiveTurnAbortRef.current = null
      const stoppedSession = proactiveSessionRef.current
      proactiveSessionRef.current = null
      listeningGate.reset()
      await realtimeAgent.abortCapture().catch(() => undefined)
      await controllerRef.current.stopSpeaking().catch(() => undefined)
      if (restoreStandby) await markStandby(reason)
      console.info('[ProximityDebug] proactive-reception-stopped', {
        reason,
        visitorSessionId: stoppedSession,
      })
    }

    const scheduleVisitorAbsence = (reason: string): void => {
      if (visitorAbsenceTimerRef.current !== null) return
      console.info('[ProximityDebug] proactive-session-absence-grace-started', {
        reason,
        visitorSessionId: activeRemoteSessionRef.current ?? proactiveSessionRef.current,
        graceMs: listeningPolicy.sessionAbsenceMs,
      })
      visitorAbsenceTimerRef.current = window.setTimeout(() => {
        visitorAbsenceTimerRef.current = null
        visitorPresentRef.current = false
        latestPrimaryDetectionRef.current = null
        listeningGate.reset()
        const absentSession = activeRemoteSessionRef.current
        if (absentSession && greetedVisitRef.current === absentSession) {
          greetedVisitRef.current = null
        }
        activeRemoteSessionRef.current = null
        if (proactiveSessionRef.current) {
          void stopProactiveReception(`${reason}_confirmed`, true)
        }
      }, listeningPolicy.sessionAbsenceMs)
    }

    const runProactiveReception = async (visitorSessionId: string): Promise<void> => {
      proactiveLoopGenerationRef.current += 1
      const generation = proactiveLoopGenerationRef.current
      proactiveSessionRef.current = visitorSessionId
      console.info('[ProximityDebug] proactive-reception-started', {
        visitorSessionId,
      })

      while (
        !disposed &&
        generation === proactiveLoopGenerationRef.current &&
        proactiveSessionRef.current === visitorSessionId &&
        visitorPresentRef.current
      ) {
        const current = controllerRef.current
        const gate = evaluateListeningGate()
        const readyForUser =
          current.conversationPhase === 'awaiting_user' &&
          current.panel === 'idle' &&
          !current.active &&
          !current.listening &&
          !current.runtime.outputActive &&
          !current.recordingActive &&
          !current.recordingSaving

        if (!readyForUser || !gate.eligible) {
          await wait(PROACTIVE_LOOP_POLL_MS)
          continue
        }

        const turnAbort = new AbortController()
        proactiveTurnAbortRef.current = turnAbort
        const result = await captureAutomaticRealtimeTurn(
          () => controllerRef.current,
          turnAbort.signal,
          evaluateListeningGate,
        )
        if (proactiveTurnAbortRef.current === turnAbort) {
          proactiveTurnAbortRef.current = null
        }

        if (
          disposed ||
          generation !== proactiveLoopGenerationRef.current ||
          proactiveSessionRef.current !== visitorSessionId ||
          !visitorPresentRef.current
        ) {
          break
        }

        if (result.kind === 'heard') {
          console.info('[ProximityDebug] proactive-reception-user-turn', {
            visitorSessionId,
            transcript: result.transcript,
          })
          if (isDecline(result.transcript)) {
            await stopProactiveReception('visitor_declined', true)
            break
          }
          await wait(PROACTIVE_LOOP_POLL_MS)
          continue
        }

        if (result.kind === 'gated') {
          console.info('[ProximityDebug] proactive-reception-listening-suspended', {
            visitorSessionId,
            reason: result.reason,
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
      latestPrimaryDetectionRef.current = detection
      visitorPresentRef.current = true
      clearVisitorAbsenceTimer()
      evaluateListeningGate()
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
          latestPrimaryDetectionRef.current = detection
          if (detection) {
            visitorPresentRef.current = true
            clearVisitorAbsenceTimer()
            evaluateListeningGate()
            return
          }
          evaluateListeningGate()
          scheduleVisitorAbsence('local_visitor_absent')
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
          latestPrimaryDetectionRef.current = detection
          if (detection) {
            visitorPresentRef.current = true
            clearVisitorAbsenceTimer()
            if (detection.visitor_session_id) {
              activeRemoteSessionRef.current = detection.visitor_session_id
            }
            evaluateListeningGate()
            return
          }
          evaluateListeningGate()
          scheduleVisitorAbsence('remote_visitor_absent')
        },
        onGreetingCandidate: (detection: RemoteVisionDetection) => {
          void attemptGreeting(detection, detection.visitor_session_id)
        },
        onSessionExpired: (visitorSessionId) => {
          if (greetedVisitRef.current === visitorSessionId) greetedVisitRef.current = null
          if (activeRemoteSessionRef.current === visitorSessionId) {
            activeRemoteSessionRef.current = null
          }
          pendingSessionsRef.current.delete(visitorSessionId)
          lastAttemptAtRef.current.delete(visitorSessionId)
          if (proactiveSessionRef.current === visitorSessionId) {
            visitorPresentRef.current = false
            latestPrimaryDetectionRef.current = null
            listeningGate.reset()
            clearVisitorAbsenceTimer()
            void stopProactiveReception('remote_session_expired', true)
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
      visitorPresentRef.current = false
      latestPrimaryDetectionRef.current = null
      listeningGate.reset()
      void realtimeAgent.abortCapture().catch(() => undefined)
      if (fallbackTimer !== null) window.clearTimeout(fallbackTimer)
      clearVisitorAbsenceTimer()
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
