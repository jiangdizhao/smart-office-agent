import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'
import { voiceOutputManager } from './voiceOutputManager'

const PATCH_FLAG = '__smartOfficeProximityTimelineDiagnosticsInstalled__'

type DiagnosticWindow = Window & {
  [PATCH_FLAG]?: boolean
}

function diagnosticsEnabled(): boolean {
  const configured = String(import.meta.env.VITE_PROXIMITY_DEBUG ?? '')
    .trim()
    .toLowerCase()
  return (
    configured === 'true' ||
    configured === '1' ||
    configured === 'on' ||
    (configured !== 'false' &&
      configured !== '0' &&
      configured !== 'off' &&
      import.meta.env.DEV)
  )
}

function errorText(error: unknown): string {
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error)
}

function runtimeSnapshot(): Record<string, unknown> {
  const status = realtimeAgent.status()
  return {
    connected: status.connected,
    connectionState: status.connectionState,
    dataChannelState: status.dataChannelState,
    microphoneAttached: status.microphoneAttached,
    responseActive: status.responseActive,
    outputActive: status.outputActive,
  }
}

function elapsed(startedAt: number): number {
  return Math.round(performance.now() - startedAt)
}

function info(event: string, data: Record<string, unknown> = {}): void {
  console.info(`[ProximityDebug] ${event}`, {
    wallTime: new Date().toISOString(),
    monotonicMs: Math.round(performance.now()),
    ...data,
  })
}

function failure(event: string, error: unknown, data: Record<string, unknown> = {}): void {
  console.error(`[ProximityDebug] ${event}`, {
    wallTime: new Date().toISOString(),
    monotonicMs: Math.round(performance.now()),
    message: errorText(error),
    ...data,
  })
}

function installRealtimeEventTracing(): void {
  const eventNames = [
    'smartoffice:realtime-speaking-start',
    'smartoffice:realtime-speaking-stop',
    'smartoffice:realtime-listening-start',
    'smartoffice:realtime-listening-stop',
    'smartoffice:realtime-connected',
    'smartoffice:realtime-visit-session-closed',
    'smartoffice:host-intro-start',
    'smartoffice:host-intro-cancel',
  ] as const

  for (const eventName of eventNames) {
    window.addEventListener(eventName, (event) => {
      const detail = event instanceof CustomEvent ? event.detail : undefined
      info('timeline-browser-event', {
        eventName,
        detail: detail ?? null,
        runtime: runtimeSnapshot(),
      })
    })
  }

  window.addEventListener('smartoffice:realtime-connection-state', (event) => {
    info('timeline-browser-event', {
      eventName: 'smartoffice:realtime-connection-state',
      detail: event instanceof CustomEvent ? event.detail ?? null : null,
      runtime: runtimeSnapshot(),
    })
  })
}

function installVoiceOutputTracing(): void {
  const originalManagerSpeak = voiceOutputManager.speak.bind(voiceOutputManager)
  voiceOutputManager.speak = async (
    text: string,
    language: VoiceLanguage,
  ): Promise<void> => {
    const startedAt = performance.now()
    info('timeline-voice-manager-speak-start', {
      text,
      language,
      provider: voiceOutputManager.selectedProvider(),
      runtime: runtimeSnapshot(),
    })
    try {
      await originalManagerSpeak(text, language)
      info('timeline-voice-manager-speak-resolved', {
        text,
        language,
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
    } catch (error) {
      failure('timeline-voice-manager-speak-rejected', error, {
        text,
        language,
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
      throw error
    }
  }

  const originalSpeakExact = realtimeAgent.speakExact.bind(realtimeAgent)
  realtimeAgent.speakExact = async (
    text: string,
    language: VoiceLanguage,
  ): Promise<string> => {
    const startedAt = performance.now()
    info('timeline-realtime-speak-exact-start', {
      text,
      language,
      runtime: runtimeSnapshot(),
    })
    try {
      const result = await originalSpeakExact(text, language)
      info('timeline-realtime-speak-exact-resolved', {
        text,
        language,
        elapsedMs: elapsed(startedAt),
        result,
        runtime: runtimeSnapshot(),
      })
      return result
    } catch (error) {
      failure('timeline-realtime-speak-exact-rejected', error, {
        text,
        language,
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
      throw error
    }
  }

  const originalStopOutput = realtimeAgent.stopOutput.bind(realtimeAgent)
  realtimeAgent.stopOutput = async (): Promise<void> => {
    const startedAt = performance.now()
    info('timeline-realtime-stop-output-start', { runtime: runtimeSnapshot() })
    try {
      await originalStopOutput()
      info('timeline-realtime-stop-output-complete', {
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
    } catch (error) {
      failure('timeline-realtime-stop-output-error', error, {
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
      throw error
    }
  }

  const originalAbortCapture = realtimeAgent.abortCapture.bind(realtimeAgent)
  realtimeAgent.abortCapture = async (): Promise<void> => {
    const startedAt = performance.now()
    info('timeline-realtime-abort-capture-start', { runtime: runtimeSnapshot() })
    try {
      await originalAbortCapture()
      info('timeline-realtime-abort-capture-complete', {
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
    } catch (error) {
      failure('timeline-realtime-abort-capture-error', error, {
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
      throw error
    }
  }

  const originalShutdown = realtimeAgent.shutdown.bind(realtimeAgent)
  realtimeAgent.shutdown = async (): Promise<void> => {
    const startedAt = performance.now()
    info('timeline-realtime-shutdown-start', { runtime: runtimeSnapshot() })
    try {
      await originalShutdown()
      info('timeline-realtime-shutdown-complete', {
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
    } catch (error) {
      failure('timeline-realtime-shutdown-error', error, {
        elapsedMs: elapsed(startedAt),
        runtime: runtimeSnapshot(),
      })
      throw error
    }
  }
}

function installBrowserSpeechTracing(): void {
  if (!('speechSynthesis' in window)) return
  const synthesis = window.speechSynthesis
  const originalSpeak = synthesis.speak.bind(synthesis)
  const originalCancel = synthesis.cancel.bind(synthesis)

  try {
    synthesis.speak = (utterance: SpeechSynthesisUtterance): void => {
      const startedAt = performance.now()
      info('timeline-browser-tts-speak-called', {
        text: utterance.text,
        lang: utterance.lang,
        rate: utterance.rate,
      })
      utterance.addEventListener('start', () => {
        info('timeline-browser-tts-started', {
          text: utterance.text,
          elapsedMs: elapsed(startedAt),
        })
      })
      utterance.addEventListener('end', () => {
        info('timeline-browser-tts-ended', {
          text: utterance.text,
          elapsedMs: elapsed(startedAt),
        })
      })
      utterance.addEventListener('error', (event) => {
        failure('timeline-browser-tts-error', event.error, {
          text: utterance.text,
          elapsedMs: elapsed(startedAt),
        })
      })
      originalSpeak(utterance)
    }
    synthesis.cancel = (): void => {
      info('timeline-browser-tts-cancel-called', {
        speaking: synthesis.speaking,
        pending: synthesis.pending,
      })
      originalCancel()
    }
  } catch (error) {
    failure('timeline-browser-tts-patch-error', error)
  }
}

export function installProximityTimelineDiagnostics(): void {
  if (!diagnosticsEnabled()) return
  const diagnosticWindow = window as DiagnosticWindow
  if (diagnosticWindow[PATCH_FLAG]) return
  diagnosticWindow[PATCH_FLAG] = true
  installRealtimeEventTracing()
  installVoiceOutputTracing()
  installBrowserSpeechTracing()
  info('timeline-diagnostics-installed', { runtime: runtimeSnapshot() })
}

installProximityTimelineDiagnostics()
