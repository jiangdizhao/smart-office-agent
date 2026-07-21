import {
  realtimeAgent as baseRealtimeAgent,
  type RealtimeRuntimeStatus,
  type VoiceLanguage,
} from './realtimeAgentRuntime'

/**
 * UI-facing facade for the shared Realtime singleton.
 *
 * The underlying runtime normally releases the physical microphone track at the
 * end of a turn. This facade also releases it when connection, commit, or
 * transcription fails, so every failed capture path has the same cleanup
 * guarantee as an explicit user abort.
 */
class SafeRealtimeAgentRuntime {
  async prewarm(language: VoiceLanguage): Promise<void> {
    await baseRealtimeAgent.prewarm(language)
  }

  async beginCapture(language: VoiceLanguage): Promise<void> {
    try {
      await baseRealtimeAgent.beginCapture(language)
    } catch (error) {
      await baseRealtimeAgent.abortCapture().catch(() => undefined)
      throw error
    }
  }

  async endCapture(): Promise<string> {
    try {
      return await baseRealtimeAgent.endCapture()
    } catch (error) {
      await baseRealtimeAgent.abortCapture().catch(() => undefined)
      throw error
    }
  }

  async abortCapture(): Promise<void> {
    await baseRealtimeAgent.abortCapture()
  }

  async speakExact(text: string, language: VoiceLanguage): Promise<string> {
    return await baseRealtimeAgent.speakExact(text, language)
  }

  async stopOutput(): Promise<void> {
    await baseRealtimeAgent.stopOutput()
  }

  status(): RealtimeRuntimeStatus {
    return baseRealtimeAgent.status()
  }

  async shutdown(): Promise<void> {
    await baseRealtimeAgent.shutdown()
  }
}

export type { RealtimeRuntimeStatus, VoiceLanguage }
export const realtimeAgent = new SafeRealtimeAgentRuntime()
