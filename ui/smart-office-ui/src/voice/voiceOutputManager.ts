import {
  realtimeAgent,
  RealtimeSpeechError,
  type VoiceLanguage,
} from './realtimeAgentRuntime'
import { visitLeaseRegistry, type VisitLease } from '../vision/visitLeaseRegistry'

export type VoiceOutputProvider = 'realtime' | 'none'

export type VoiceSpeakOptions = {
  lease?: VisitLease | null
  signal?: AbortSignal
  fixedLocal?: boolean
  allowLocalFallback?: boolean
}

const STORAGE_KEY = 'smartoffice_voice_output_provider'
const LOCAL_SPEECH_TIMEOUT_MS = 30_000

function storedProvider(): VoiceOutputProvider {
  return localStorage.getItem(STORAGE_KEY) === 'none' ? 'none' : 'realtime'
}

function detectedSpeechLanguage(text: string, selected: VoiceLanguage): VoiceLanguage {
  const chineseCharacters = (text.match(/[\u3400-\u9fff]/g) ?? []).length
  const latinWords = (text.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g) ?? []).length
  if (chineseCharacters === 0 && latinWords > 0) return 'en'
  if (chineseCharacters > 0) return 'zh'
  return selected
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

export class VoiceOutputManager {
  private provider: VoiceOutputProvider = storedProvider()
  private speechGeneration = 0

  selectedProvider(): VoiceOutputProvider {
    return this.provider
  }

  async setProvider(provider: VoiceOutputProvider): Promise<void> {
    if (provider === this.provider) return
    await this.stop()
    this.provider = provider
    localStorage.setItem(STORAGE_KEY, provider)
    window.dispatchEvent(
      new CustomEvent('smartoffice:voice-output-provider-changed', { detail: provider }),
    )
  }

  async speak(
    text: string,
    language: VoiceLanguage,
    options: VoiceSpeakOptions = {},
  ): Promise<void> {
    const clean = text.trim()
    if (!clean || this.provider === 'none') return
    const lease = options.lease ?? visitLeaseRegistry.current()
    const signal = options.signal ?? lease?.signal
    if (signal?.aborted) throw abortError('Speech operation was aborted before it started.')
    if (lease && !visitLeaseRegistry.isCurrent(lease)) {
      throw abortError('Speech belongs to a stale visit.')
    }

    const generation = ++this.speechGeneration
    await this.stopInternal(false)
    if (options.fixedLocal) {
      await this.speakLocal(clean, language, generation, signal)
      return
    }

    try {
      await realtimeAgent.speakExact(
        clean,
        detectedSpeechLanguage(clean, language),
        signal,
      )
      this.assertSpeechCurrent(generation, lease, signal)
    } catch (error) {
      const audioStarted = error instanceof RealtimeSpeechError && error.audioStarted
      const aborted = error instanceof Error && error.name === 'AbortError'
      if (aborted || signal?.aborted || generation !== this.speechGeneration) throw error
      // Once Realtime audio has started, a second TTS is forbidden even when
      // the final response event reports an error.
      if (audioStarted || options.allowLocalFallback === false) throw error
      await this.speakLocal(clean, language, generation, signal)
    }
  }

  async speakFixed(
    text: string,
    language: VoiceLanguage,
    lease?: VisitLease | null,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.speak(text, language, {
      lease,
      signal,
      fixedLocal: true,
      allowLocalFallback: false,
    })
  }

  async stop(): Promise<void> {
    this.speechGeneration += 1
    await this.stopInternal(true)
  }

  private async stopInternal(cancelLocal: boolean): Promise<void> {
    if (cancelLocal && 'speechSynthesis' in window) window.speechSynthesis.cancel()
    await realtimeAgent.stopOutput().catch(() => undefined)
  }

  private assertSpeechCurrent(
    generation: number,
    lease: VisitLease | null,
    signal?: AbortSignal,
  ): void {
    if (signal?.aborted || generation !== this.speechGeneration) {
      throw abortError('Speech operation was superseded.')
    }
    if (lease && !visitLeaseRegistry.isCurrent(lease)) {
      throw abortError('Speech belongs to a stale visit.')
    }
  }

  private speakLocal(
    text: string,
    language: VoiceLanguage,
    generation: number,
    signal?: AbortSignal,
  ): Promise<void> {
    if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') {
      return Promise.reject(new Error('Local browser speech synthesis is unavailable.'))
    }
    return new Promise((resolve, reject) => {
      const utterance = new SpeechSynthesisUtterance(text)
      utterance.lang = language === 'zh' ? 'zh-CN' : 'en-AU'
      utterance.rate = language === 'zh' ? 0.94 : 1
      let settled = false
      const finish = (error?: Error) => {
        if (settled) return
        settled = true
        window.clearTimeout(timer)
        signal?.removeEventListener('abort', onAbort)
        if (error) reject(error)
        else resolve()
      }
      const onAbort = () => {
        window.speechSynthesis.cancel()
        finish(abortError('Local speech was aborted.'))
      }
      const timer = window.setTimeout(() => {
        window.speechSynthesis.cancel()
        finish(new Error('Local speech timed out.'))
      }, LOCAL_SPEECH_TIMEOUT_MS)
      utterance.onstart = () => {
        try {
          this.assertSpeechCurrent(generation, null, signal)
        } catch (error) {
          window.speechSynthesis.cancel()
          finish(error instanceof Error ? error : new Error(String(error)))
        }
      }
      utterance.onend = () => finish()
      utterance.onerror = (event) => {
        finish(new Error(`Local speech failed: ${event.error}`))
      }
      signal?.addEventListener('abort', onAbort, { once: true })
      window.speechSynthesis.cancel()
      window.speechSynthesis.speak(utterance)
    })
  }
}

export const voiceOutputManager = new VoiceOutputManager()
