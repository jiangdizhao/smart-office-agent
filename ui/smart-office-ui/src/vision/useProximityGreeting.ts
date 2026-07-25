import { useEffect, useRef, useState } from 'react'
import type { OfficeVoiceController } from '../voice/useOfficeVoiceController'
import {
  ProximityFaceMonitor,
  type ProximityDetection,
  type ProximityDetectorStatus,
} from './proximityFaceMonitor'

const ENABLED_KEY = 'smartoffice_proximity_greeting_enabled'

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
    () => localStorage.getItem(ENABLED_KEY) !== 'false',
  )
  const [status, setStatus] = useState<ProximityDetectorStatus | 'disabled'>(
    enabled ? 'starting' : 'disabled',
  )
  const [detail, setDetail] = useState('')
  const [lastDetection, setLastDetection] = useState<ProximityDetection | null>(null)
  const controllerRef = useRef(controller)
  const previousConversationPhaseRef = useRef(controller.conversationPhase)
  const monitorRef = useRef<ProximityFaceMonitor | null>(null)

  useEffect(() => {
    controllerRef.current = controller
  }, [controller])

  useEffect(() => {
    const previousPhase = previousConversationPhaseRef.current
    const nextPhase = controller.conversationPhase
    previousConversationPhaseRef.current = nextPhase

    if (previousPhase === 'standby' && nextPhase !== 'standby') {
      monitorRef.current?.suppressUntilAbsent()
    }
  }, [controller.conversationPhase])

  useEffect(() => {
    if (!enabled) {
      setStatus('disabled')
      setDetail('')
      monitorRef.current?.stop()
      monitorRef.current = null
      return
    }

    const monitor = new ProximityFaceMonitor(
      () => {
        const current = controllerRef.current
        return (
          current.conversationPhase === 'standby' &&
          current.panel === 'idle' &&
          !current.active &&
          !current.listening &&
          !current.runtime.outputActive &&
          !current.runtime.microphoneAttached
        )
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
    localStorage.setItem(ENABLED_KEY, String(next))
    setEnabledState(next)
  }

  return {
    enabled,
    status,
    detail,
    lastDetection,
    setEnabled,
  }
}
