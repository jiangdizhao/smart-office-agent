import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'

const PATCH_FLAG = '__smartOfficeCaptureCleanupInstalled__'

type GuardedRealtimeAgent = typeof realtimeAgent & {
  [PATCH_FLAG]?: boolean
}

/**
 * Install a single cleanup guard around the shared Realtime capture methods.
 *
 * The normal success path already replaces the physical microphone with the
 * silent WebRTC track. The guard covers exceptional paths such as connection,
 * audio-buffer commit, or transcription failures and guarantees that the real
 * microphone track is stopped before the error reaches the UI.
 */
export function installRealtimeCaptureCleanup(): void {
  const guardedAgent = realtimeAgent as GuardedRealtimeAgent
  if (guardedAgent[PATCH_FLAG]) return

  const originalBeginCapture = realtimeAgent.beginCapture.bind(realtimeAgent)
  const originalEndCapture = realtimeAgent.endCapture.bind(realtimeAgent)

  realtimeAgent.beginCapture = async (language: VoiceLanguage): Promise<void> => {
    try {
      await originalBeginCapture(language)
    } catch (error) {
      await realtimeAgent.abortCapture().catch(() => undefined)
      throw error
    }
  }

  realtimeAgent.endCapture = async (): Promise<string> => {
    try {
      return await originalEndCapture()
    } catch (error) {
      await realtimeAgent.abortCapture().catch(() => undefined)
      throw error
    }
  }

  guardedAgent[PATCH_FLAG] = true
}

installRealtimeCaptureCleanup()
