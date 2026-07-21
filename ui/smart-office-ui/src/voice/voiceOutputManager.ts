import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'

export type VoiceOutputProvider = 'realtime' | 'none'

const STORAGE_KEY = 'smartoffice_voice_output_provider'

function storedProvider(): VoiceOutputProvider {
  return localStorage.getItem(STORAGE_KEY) === 'none' ? 'none' : 'realtime'
}

export class VoiceOutputManager {
  private provider: VoiceOutputProvider = storedProvider()

  selectedProvider(): VoiceOutputProvider {
    return this.provider
  }

  async setProvider(provider: VoiceOutputProvider): Promise<void> {
    if (provider === this.provider) return
    await this.stop()
    this.provider = provider
    localStorage.setItem(STORAGE_KEY, provider)
    window.dispatchEvent(
      new CustomEvent('smartoffice:voice-output-provider-changed', {
        detail: provider,
      }),
    )
  }

  async speak(text: string, language: VoiceLanguage): Promise<void> {
    const clean = text.trim()
    if (!clean || this.provider === 'none') return
    if (this.provider === 'realtime') {
      await realtimeAgent.speakExact(clean, language)
      return
    }
    throw new Error(`Unsupported voice output provider: ${this.provider}`)
  }

  async stop(): Promise<void> {
    await realtimeAgent.stopOutput()
  }
}

export const voiceOutputManager = new VoiceOutputManager()
