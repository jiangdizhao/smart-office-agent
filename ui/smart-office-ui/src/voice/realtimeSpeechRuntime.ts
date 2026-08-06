import {
  PersistentRealtimeAgent,
  realtimeAgent,
} from './realtimeAgentRuntime'

const GLOBAL_AGENT_KEY = '__smartOfficeRealtimeSpeechAgent__'
const GLOBAL_PATCH_KEY = '__smartOfficeRealtimeSpeechBridgeInstalled__'

type SpeechRuntimeWindow = typeof window & {
  [GLOBAL_AGENT_KEY]?: PersistentRealtimeAgent
  [GLOBAL_PATCH_KEY]?: boolean
}

function separateSpeechEnabled(): boolean {
  return String(import.meta.env.VITE_REALTIME_SEPARATE_SPEECH_SESSION ?? 'true')
    .trim()
    .toLocaleLowerCase() !== 'false'
}

const runtimeWindow = window as SpeechRuntimeWindow
const separate = separateSpeechEnabled()
const dedicatedAgent = separate
  ? runtimeWindow[GLOBAL_AGENT_KEY] ?? new PersistentRealtimeAgent()
  : realtimeAgent

if (separate) runtimeWindow[GLOBAL_AGENT_KEY] = dedicatedAgent

export const realtimeSpeechAgent = dedicatedAgent
export const realtimeSpeechUsesDedicatedSession = separate

if (separate && !runtimeWindow[GLOBAL_PATCH_KEY]) {
  runtimeWindow[GLOBAL_PATCH_KEY] = true
  const originalPrimaryStopOutput = realtimeAgent.stopOutput.bind(realtimeAgent)
  const originalPrimaryShutdown = realtimeAgent.shutdown.bind(realtimeAgent)

  // Existing callers already use realtimeAgent.stopOutput as the global speech
  // cancellation point. Preserve that contract while moving TTS onto its own
  // WebRTC response slot, so ASR transcription and TTS can no longer deadlock each
  // other through one shared pendingResponse.
  realtimeAgent.stopOutput = async (): Promise<void> => {
    await Promise.allSettled([
      originalPrimaryStopOutput(),
      realtimeSpeechAgent.stopOutput(),
    ])
  }

  realtimeAgent.shutdown = async (): Promise<void> => {
    await Promise.allSettled([
      realtimeSpeechAgent.shutdown(),
      originalPrimaryShutdown(),
    ])
  }

  window.addEventListener('smartoffice:visit-revoked', () => {
    void realtimeSpeechAgent.shutdown().catch(() => undefined)
  })
}
