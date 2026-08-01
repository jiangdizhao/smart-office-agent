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
const MAX_CHINESE_CHUNK_CHARS = 82
const MAX_ENGLISH_CHUNK_CHARS = 250

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

function hardSplit(text: string, maxChars: number): string[] {
  const result: string[] = []
  let remaining = text.trim()
  while (remaining.length > maxChars) {
    const search = remaining.slice(0, maxChars + 1)
    const candidates = [
      search.lastIndexOf('，'), search.lastIndexOf(','), search.lastIndexOf('；'),
      search.lastIndexOf(';'), search.lastIndexOf('：'), search.lastIndexOf(':'),
      search.lastIndexOf(' '),
    ]
    const splitAt = Math.max(...candidates)
    const index = splitAt >= Math.floor(maxChars * 0.45) ? splitAt + 1 : maxChars
    result.push(remaining.slice(0, index).trim())
    remaining = remaining.slice(index).trim()
  }
  if (remaining) result.push(remaining)
  return result
}

function speechChunks(text: string, language: VoiceLanguage): string[] {
  const clean = text.replace(/\s+/g, ' ').trim()
  if (!clean) return []
  const maxChars = language === 'zh' ? MAX_CHINESE_CHUNK_CHARS : MAX_ENGLISH_CHUNK_CHARS
  if (clean.length <= maxChars) return [clean]

  const sentenceParts = clean.match(/[^。！？!?\n]+[。！？!?]?/g) ?? [clean]
  const chunks: string[] = []
  let current = ''
  const flush = () => {
    if (!current.trim()) return
    chunks.push(...hardSplit(current.trim(), maxChars))
    current = ''
  }
  for (const part of sentenceParts) {
    const sentence = part.trim()
    if (!sentence) continue
    if (!current) {
      current = sentence
      continue
    }
    if (`${current} ${sentence}`.length <= maxChars) {
      current = `${current} ${sentence}`
    } else {
      flush()
      current = sentence
    }
  }
  flush()
  return chunks.length ? chunks : hardSplit(clean, maxChars)
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
    await this.stopInternal(true)
    const chunks = speechChunks(clean, detectedSpeechLanguage(clean, language))
    console.info('[RealtimeDiagnostics] speech-chunk-plan', {
      generation,
      totalCharacters: clean.length,
      chunkCount: chunks.length,
      chunkLengths: chunks.map((chunk) => chunk.length),
    })

    for (let index = 0; index < chunks.length; index += 1) {
      const chunk = chunks[index]
      this.assertSpeechCurrent(generation, lease, signal)
      const chunkLanguage = detectedSpeechLanguage(chunk, language)
      window.dispatchEvent(new CustomEvent('smartoffice:voice-chunk-start', {
        detail: { index, count: chunks.length, text: chunk },
      }))
      if (options.fixedLocal) {
        await this.speakLocal(chunk, chunkLanguage, generation, signal)
        continue
      }
      try {
        await realtimeAgent.speakExact(chunk, chunkLanguage, signal)
        this.assertSpeechCurrent(generation, lease, signal)
      } catch (error) {
        const audioStarted = error instanceof RealtimeSpeechError && error.audioStarted
        const aborted = error instanceof Error && error.name === 'AbortError'
        const runtime = realtimeAgent.status()
        const visitorBargeIn = Boolean(
          aborted &&
          !signal?.aborted &&
          generation === this.speechGeneration &&
          runtime.continuousListening &&
          runtime.speechDetected
        )
        if (visitorBargeIn) {
          console.info('[RealtimeDiagnostics] speech-chunks-cancelled-by-visitor', {
            generation,
            interruptedChunkIndex: index,
            chunkCount: chunks.length,
          })
          return
        }
        if (aborted || signal?.aborted || generation !== this.speechGeneration) throw error
        if (audioStarted || options.allowLocalFallback === false) throw error
        await this.speakLocal(chunk, chunkLanguage, generation, signal)
      }
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
      utterance.onerror = (event) => finish(new Error(`Local speech failed: ${event.error}`))
      signal?.addEventListener('abort', onAbort, { once: true })
      window.speechSynthesis.cancel()
      window.speechSynthesis.speak(utterance)
    })
  }
}

export const voiceOutputManager = new VoiceOutputManager()
