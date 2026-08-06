import { useCallback, useEffect, useRef } from 'react'
import type {
  OfficeVoiceController,
  ProximityDetection,
} from './useOfficeVoiceController'
import type { VoiceLanguage } from './realtimeAgentRuntime'
import {
  VISIT_LANGUAGE_CHANGED_EVENT,
  visitLanguagePreference,
  type VisitLanguageChangeDetail,
} from './visitLanguagePreference'

function afterReactCommit(): Promise<void> {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => resolve())
  })
}

/**
 * Keeps the legacy Office controller aligned with the Visit-level language owner.
 *
 * The underlying controller still contains older per-utterance heuristics. This
 * wrapper makes the Visit preference authoritative before capture, routing,
 * greetings and text submission, without duplicating the Office execution logic.
 */
export function useVisitLanguageAwareOfficeVoiceController(
  controller: OfficeVoiceController,
): OfficeVoiceController {
  const controllerRef = useRef(controller)
  controllerRef.current = controller

  const synchronise = useCallback(async (language: VoiceLanguage): Promise<void> => {
    if (controllerRef.current.language === language) return
    controllerRef.current.setLanguage(language)
    await afterReactCommit()
  }, [])

  useEffect(() => {
    void synchronise(visitLanguagePreference.current())
    const onLanguageChanged = (event: Event) => {
      const detail = event instanceof CustomEvent
        ? event.detail as VisitLanguageChangeDetail
        : null
      if (detail?.language) void synchronise(detail.language)
    }
    window.addEventListener(VISIT_LANGUAGE_CHANGED_EVENT, onLanguageChanged)
    return () => window.removeEventListener(VISIT_LANGUAGE_CHANGED_EVENT, onLanguageChanged)
  }, [synchronise])

  const setLanguage = useCallback((language: VoiceLanguage): void => {
    visitLanguagePreference.force(language, 'controller_language_selection')
  }, [])

  const connect = useCallback(async (): Promise<void> => {
    await synchronise(visitLanguagePreference.current())
    await controllerRef.current.connect()
  }, [synchronise])

  const beginListening = useCallback(async (): Promise<void> => {
    await synchronise(visitLanguagePreference.current())
    await controllerRef.current.beginListening()
  }, [synchronise])

  const submit = useCallback(async (
    text: string,
    source: 'text' | 'voice' = 'text',
  ): Promise<void> => {
    const language = visitLanguagePreference.resolve(
      text,
      visitLanguagePreference.current(),
    )
    await synchronise(language)
    await controllerRef.current.submit(text, source)
  }, [synchronise])

  const triggerProximityGreeting = useCallback(async (
    detection: ProximityDetection,
  ): Promise<boolean> => {
    await synchronise(visitLanguagePreference.current())
    return await controllerRef.current.triggerProximityGreeting(detection)
  }, [synchronise])

  return {
    ...controller,
    language: visitLanguagePreference.current(),
    setLanguage,
    connect,
    beginListening,
    submit,
    triggerProximityGreeting,
  }
}
