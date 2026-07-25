import { useEffect, useRef, useState } from 'react'
import type { OfficeVoiceController } from '../voice/useOfficeVoiceController'
import {
  ProximityFaceMonitor,
  type ProximityDetection,
  type ProximityDetectorStatus,
} from './proximityFaceMonitor'

const ENABLED_KEY = 'smartoffice_proximity_greeting_enabled'

function greetFeatureEnabled(): boolean {
  const configured = String(import.meta.env.enable_greet ?? 'true').trim().toLowerCase()
  return !['false', '0', 'off', 'no'].includes(configured)
}

const GREET_FEATURE_ENABLED = greetFeatureEnabled()

export type ProximityGreetingController = {
  enabled: boolean
  status: ProximityDetectorStatus | 'disabled'
  detail: string
  lastDetection: ProximityDetection | null
  setEnabled: (enabled: boolean) => void
}

export function useProximityGreeting(
  controller: OfficeVoiceController,
): ProximityGreetingController {
  const [enabled, setEnabledState] = useState(
    () => GREET_FEATURE_ENABLED && localStorage.getItem(ENABLED_KEY) !== 'false',
  )
  const [status, setStatus] = useState<ProximityDetectorStatus | 'disabled'>(
    enabled ? 'starting' : 'disabled',
  )
  const [detail, setDetail] = useState(
    GREET_FEATURE_ENABLED ? '' : 'disabled by enable_greet=false',
  )
  const [lastDetection, setLastDetection] = useState<ProximityDetection | null>(null)
  const controllerRef = useRef(controller)
  const previousConversationPhaseRef = useRef(controller.conversationPhase)
  const lastEligibilitySignatureRef = useRef('')
  const monitorRef = useRef<ProximityFaceMonitor | null>(null)

  useEffect(() => {
    controllerRef.current = controller
  }, [controller])

  useEffect(() => {
    if (!GREET_FEATURE_ENABLED) return

    const previousPhase = previousConversationPhaseRef.current
    const nextPhase = controller.conversationPhase
    previousConversationPhaseRef.current = nextPhase

    if (previousPhase === 'standby' && nextPhase !== 'standby') {
      monitorRef.current?.suppressUntilAbsent()
    }
  }, [controller.conversationPhase])

  useEffect(() => {
    if (!GREET_FEATURE_ENABLED || !enabled) {
      setStatus('disabled')
      setDetail(
        GREET_FEATURE_ENABLED ? '' : 'disabled by enable_greet=false; camera and detectors not started',
      )
      setLastDetection(null)
      monitorRef.current?.stop()
      monitorRef.current = null
      return
    }

    const monitor = new ProximityFaceMonitor(
      () => {
        const current = controllerRef.current
        const eligibility = {
          conversationPhase: current.conversationPhase,
          panel: current.panel,
          active: current.active,
          listening: current.listening,
          outputActive: current.runtime.outputActive,
          microphoneAttached: current.runtime.microphoneAttached,
        }
        const phaseAllowsGreeting =
          eligibility.conversationPhase === 'standby' ||
          eligibility.conversationPhase === 'awaiting_user'
        const eligible =
          phaseAllowsGreeting &&
          eligibility.panel === 'idle' &&
          !eligibility.active &&
          !eligibility.listening &&
          !eligibility.outputActive

        const signature = JSON.stringify({ ...eligibility, phaseAllowsGreeting, eligible })
        if (signature !== lastEligibilitySignatureRef.current) {
          lastEligibilitySignatureRef.current = signature
          console.info('[ProximityDebug] frontend-eligibility', {
            ...eligibility,
            phaseAllowsGreeting,
            eligible,
            note:
              'standby and awaiting_user are allowed; microphoneAttached is diagnostic only',
          })
        }
        return eligible
      },
      async (detection) => await controllerRef.current.triggerProximityGreeting(detection),
      (nextStatus, nextDetail = '') => {
        setStatus(nextStatus)
        setDetail(nextDetail)
      },
      setLastDetection,
    )
    monitorRef.current = monitor
    void monitor.start()

    return () => {
      monitor.stop()
      if (monitorRef.current === monitor) monitorRef.current = null
    }
  }, [enabled])

  function setEnabled(next: boolean): void {
    if (!GREET_FEATURE_ENABLED) {
      localStorage.setItem(ENABLED_KEY, 'false')
      setEnabledState(false)
      return
    }
    localStorage.setItem(ENABLED_KEY, String(next))
    setEnabledState(next)
  }

  return {
    enabled: GREET_FEATURE_ENABLED && enabled,
    status,
    detail,
    lastDetection,
    setEnabled,
  }
}
