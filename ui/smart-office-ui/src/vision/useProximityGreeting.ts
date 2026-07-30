import { useEffect, useRef, useState } from 'react'
import type { OfficeVoiceController } from '../voice/useOfficeVoiceController'
import {
  ProximityFaceMonitor,
  type ProximityDetection,
  type ProximityDetectorStatus,
} from './proximityFaceMonitor'
import {
  RemoteVisionClient,
  remoteVisionUrl,
  type RemoteVisionDetection,
  type RemoteVisionStatus,
} from './remoteVisionClient'

const ENABLED_KEY = 'smartoffice_proximity_greeting_enabled'
const REMOTE_FALLBACK_DELAY_MS = 8_000
const REMOTE_ATTEMPT_COOLDOWN_MS = 2_000

type VisionSourceMode = 'remote' | 'remote-with-fallback' | 'mediapipe' | 'disabled'
export type ProximitySource = 'remote' | 'mediapipe' | 'disabled'
export type ProximityStatus =
  | ProximityDetectorStatus
  | RemoteVisionStatus
  | 'fallback'
  | 'disabled'

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
  const greetedSessionsRef = useRef(new Set<string>())
  const pendingSessionsRef = useRef(new Set<string>())
  const lastAttemptAtRef = useRef(new Map<string, number>())
  const localGreetingInFlightRef = useRef(false)

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
      return
    }

    let disposed = false
    let fallbackTimer: number | null = null

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
      detection: ProximityDetection,
      visitorSessionId = '',
    ): Promise<boolean> => {
      if (!eligibleNow()) return false
      if (visitorSessionId && greetedSessionsRef.current.has(visitorSessionId)) return false
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
      window.dispatchEvent(
        new CustomEvent('smartoffice:host-intro-start', {
          detail: { detection, welcomeText: 'Welcome to our office.' },
        }),
      )
      try {
        const triggered = await controllerRef.current.triggerProximityGreeting(detection)
        if (triggered && visitorSessionId) greetedSessionsRef.current.add(visitorSessionId)
        if (!triggered) window.dispatchEvent(new CustomEvent('smartoffice:host-intro-cancel'))
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
        setLastDetection,
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
        onDetection: setLastDetection,
        onGreetingCandidate: (detection: RemoteVisionDetection) => {
          void attemptGreeting(detection, detection.visitor_session_id)
        },
        onSessionExpired: (visitorSessionId) => {
          greetedSessionsRef.current.delete(visitorSessionId)
          pendingSessionsRef.current.delete(visitorSessionId)
          lastAttemptAtRef.current.delete(visitorSessionId)
        },
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
