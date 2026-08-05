import { realtimeAgent, RealtimeSpeechError, type VoiceLanguage } from './realtimeAgentRuntime'
import { visitLeaseRegistry, type VisitLease } from '../vision/visitLeaseRegistry'
import {
  consumeVoiceOutputContext,
  type VoiceDeliveryPlan,
  type VoiceOutputContext,
} from '../sales/voiceDelivery'
import { speakExpressiveExact } from './expressiveRealtimeSpeech'
import { recordVoiceInterruptionDiagnostic } from './voiceInterruptionDiagnostics'

export type VoiceOutputProvider = 'realtime' | 'none'
export type AssistantOutputResult = 'started' | 'completed' | 'interrupted' | 'failed'

export type VoiceSpeakOptions = {
  lease?: VisitLease | null
  signal?: AbortSignal
  fixedLocal?: boolean
  allowLocalFallback?: boolean
  delivery?: VoiceDeliveryPlan
  purpose?: string
  replyMode?: string
  expectUserResponse?: boolean
  questionField?: string | null
}

export type AssistantOutputLifecycleDetail = {
  outputId: string
  visitId: string | null
  purpose: string
  replyMode: string
  expectUserResponse: boolean
  questionField: string | null
  deliveryStyle: string
  result: AssistantOutputResult
  text: string
  error?: string
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
    const isQuestion = /[？?]$/.test(sentence)
    if (isQuestion) {
      flush()
      chunks.push(...hardSplit(sentence, maxChars))
      continue
    }
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

function lifecycleEventName(result: AssistantOutputResult): string {
  return `smartoffice:assistant-output-${result}`
}

function outputContext(
  text: string,
  language: VoiceLanguage,
  options: VoiceSpeakOptions,
): VoiceOutputContext {
  const registered = consumeVoiceOutputContext(text, language)
  return {
    delivery: options.delivery ?? registered.delivery,
    purpose: options.purpose ?? registered.purpose,
    replyMode: options.replyMode ?? registered.replyMode,
    expectUserResponse: options.expectUserResponse ?? registered.expectUserResponse,
    questionField: options.questionField === undefined
      ? registered.questionField
      : options.questionField,
  }
}

export class VoiceOutputManager {
  private provider: VoiceOutputProvider = storedProvider()
  private speechGeneration = 0

  selectedProvider(): VoiceOutputProvider {
    return this.provider
  }

  async setProvider(provider: VoiceOutputProvider): Promise<void> {
    if (provider === this.provider) return
    await this.stop('provider-change')
    this.provider = provider
    localStorage.setItem(STORAGE_KEY, provider)
    window.dispatchEvent(
      new CustomEvent('smartoffice:voice-output-provider-changed', { detail: provider }),
    )
  }

  private dispatchLifecycle(
    result: AssistantOutputResult,
    detail: Omit<AssistantOutputLifecycleDetail, 'result'>,
  ): void {
    window.dispatchEvent(new CustomEvent(lifecycleEventName(result), {
      detail: { ...detail, result } satisfies AssistantOutputLifecycleDetail,
    }))
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

    const context = outputContext(clean, language, options)
    const outputId = crypto.randomUUID()
    const lifecycleBase = {
      outputId,
      visitId: lease?.visitId ?? null,
      purpose: context.purpose,
      replyMode: context.replyMode,
      expectUserResponse: context.expectUserResponse,
      questionField: context.questionField,
      deliveryStyle: context.delivery.style,
      text: clean,
    }
    const generation = ++this.speechGeneration
    await this.stopInternal(true, 'superseded-before-new-output')
    const chunks = speechChunks(clean, detectedSpeechLanguage(clean, language))
    console.info('[RealtimeDiagnostics] speech-chunk-plan', {
      generation,
      outputId,
      purpose: context.purpose,
      deliveryStyle: context.delivery.style,
      totalCharacters: clean.length,
      chunkCount: chunks.length,
      chunkLengths: chunks.map((chunk) => chunk.length),
    })
    recordVoiceInterruptionDiagnostic('output-started', {
      outputId,
      generation,
      visitId: lease?.visitId ?? null,
      purpose: context.purpose,
      textLength: clean.length,
      chunkCount: chunks.length,
      chunkLengths: chunks.map((chunk) => chunk.length),
      continuousListening: realtimeAgent.status().continuousListening ?? false,
    })
    this.dispatchLifecycle('started', lifecycleBase)

    try {
      for (let index = 0; index < chunks.length; index += 1) {
        const chunk = chunks[index]
        this.assertSpeechCurrent(generation, lease, signal)
        const chunkLanguage = detectedSpeechLanguage(chunk, language)
        recordVoiceInterruptionDiagnostic('chunk-started', {
          outputId,
          generation,
          chunkIndex: index,
          chunkCount: chunks.length,
          chunkLength: chunk.length,
          chunkPreview: chunk,
          runtime: realtimeAgent.status(),
        })
        window.dispatchEvent(new CustomEvent('smartoffice:voice-chunk-start', {
          detail: { index, count: chunks.length, text: chunk, outputId },
        }))
        if (options.fixedLocal) {
          await this.speakLocal(chunk, chunkLanguage, generation, signal)
          recordVoiceInterruptionDiagnostic('chunk-completed', {
            outputId, generation, chunkIndex: index, provider: 'local',
          })
          continue
        }
        try {
          await speakExpressiveExact(chunk, chunkLanguage, context.delivery, signal)
          this.assertSpeechCurrent(generation, lease, signal)
          recordVoiceInterruptionDiagnostic('chunk-completed', {
            outputId, generation, chunkIndex: index, provider: 'realtime', runtime: realtimeAgent.status(),
          })
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
              outputId,
              interruptedChunkIndex: index,
              chunkCount: chunks.length,
            })
            recordVoiceInterruptionDiagnostic('visitor-barge-in-interruption', {
              outputId,
              generation,
              interruptedChunkIndex: index,
              chunkCount: chunks.length,
              audioStarted,
              signalAborted: signal?.aborted ?? false,
              errorName: error instanceof Error ? error.name : typeof error,
              errorMessage: error instanceof Error ? error.message : String(error),
              runtime,
            })
            this.dispatchLifecycle('interrupted', lifecycleBase)
            return
          }
          if (aborted || signal?.aborted || generation !== this.speechGeneration) {
            recordVoiceInterruptionDiagnostic('chunk-aborted-without-barge-in', {
              outputId,
              generation,
              interruptedChunkIndex: index,
              currentGeneration: this.speechGeneration,
              audioStarted,
              signalAborted: signal?.aborted ?? false,
              errorName: error instanceof Error ? error.name : typeof error,
              errorMessage: error instanceof Error ? error.message : String(error),
              runtime,
            })
            throw error
          }
          if (audioStarted || options.allowLocalFallback === false) {
            recordVoiceInterruptionDiagnostic('chunk-failed-no-fallback', {
              outputId,
              generation,
              chunkIndex: index,
              audioStarted,
              allowLocalFallback: options.allowLocalFallback ?? true,
              errorMessage: error instanceof Error ? error.message : String(error),
              runtime,
            })
            throw error
          }
          recordVoiceInterruptionDiagnostic('chunk-local-fallback', {
            outputId,
            generation,
            chunkIndex: index,
            errorMessage: error instanceof Error ? error.message : String(error),
          })
          await this.speakLocal(chunk, chunkLanguage, generation, signal)
        }
      }
      recordVoiceInterruptionDiagnostic('output-completed', {
        outputId, generation, chunkCount: chunks.length, runtime: realtimeAgent.status(),
      })
      this.dispatchLifecycle('completed', lifecycleBase)
    } catch (error) {
      const aborted = error instanceof Error && error.name === 'AbortError'
      recordVoiceInterruptionDiagnostic(aborted ? 'output-aborted' : 'output-failed', {
        outputId,
        generation,
        currentGeneration: this.speechGeneration,
        signalAborted: signal?.aborted ?? false,
        errorName: error instanceof Error ? error.name : typeof error,
        errorMessage: error instanceof Error ? error.message : String(error),
        runtime: realtimeAgent.status(),
      })
      this.dispatchLifecycle(aborted ? 'interrupted' : 'failed', {
        ...lifecycleBase,
        error: error instanceof Error ? error.message : String(error),
      })
      throw error
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
      purpose: 'fixed_local_output',
      replyMode: 'exact_operational',
      expectUserResponse: false,
    })
  }

  async stop(reason = 'external-stop'): Promise<void> {
    const previousGeneration = this.speechGeneration
    this.speechGeneration += 1
    recordVoiceInterruptionDiagnostic('stop-requested', {
      reason,
      previousGeneration,
      currentGeneration: this.speechGeneration,
      runtime: realtimeAgent.status(),
    })
    await this.stopInternal(true, reason)
  }

  private async stopInternal(cancelLocal: boolean, reason: string): Promise<void> {
    const runtimeBefore = realtimeAgent.status()
    if (cancelLocal && 'speechSynthesis' in window) window.speechSynthesis.cancel()
    await realtimeAgent.stopOutput().catch((error) => {
      recordVoiceInterruptionDiagnostic('stop-output-error', {
        reason,
        errorMessage: error instanceof Error ? error.message : String(error),
        runtimeBefore,
      })
      return undefined
    })
    if (runtimeBefore.outputActive || runtimeBefore.responseActive) {
      recordVoiceInterruptionDiagnostic('active-output-stopped', {
        reason,
        cancelLocal,
        runtimeBefore,
        runtimeAfter: realtimeAgent.status(),
      })
    }
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
