import type { VoiceLanguage } from './realtimeAgentRuntime'

type BrowserSpeechRecognitionAlternative = {
  transcript: string
  confidence?: number
}

type BrowserSpeechRecognitionResult = {
  isFinal?: boolean
  length: number
  [index: number]: BrowserSpeechRecognitionAlternative
}

type BrowserSpeechRecognitionEvent = {
  resultIndex: number
  results: {
    length: number
    [index: number]: BrowserSpeechRecognitionResult
  }
}

type BrowserSpeechRecognitionErrorEvent = {
  error: string
  message?: string
}

type BrowserSpeechRecognition = {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start: () => void
  stop: () => void
  abort: () => void
  onstart: (() => void) | null
  onend: (() => void) | null
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null
}

type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition
type TranscriptListener = (partial: string) => void

const START_TIMEOUT_MS = 5_000
const STOP_TIMEOUT_MS = 4_000

function recognitionConstructor(): BrowserSpeechRecognitionConstructor | null {
  const speechWindow = window as Window & {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor
  }
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null
}

function joinTranscript(...parts: string[]): string {
  return parts
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
}

export class BrowserSpeechCapture {
  private recognition: BrowserSpeechRecognition | null = null
  private finalTranscript = ''
  private interimTranscript = ''
  private resolveStart: (() => void) | null = null
  private rejectStart: ((error: Error) => void) | null = null
  private resolveStop: ((transcript: string) => void) | null = null
  private rejectStop: ((error: Error) => void) | null = null
  private startTimer: number | null = null
  private stopTimer: number | null = null
  private onTranscript: TranscriptListener | null = null
  private active = false

  available(): boolean {
    return recognitionConstructor() !== null
  }

  begin(language: VoiceLanguage, onTranscript: TranscriptListener): Promise<void> {
    if (this.active || this.recognition) {
      return Promise.reject(new Error('Browser speech capture is already active.'))
    }
    const Recognition = recognitionConstructor()
    if (!Recognition) {
      return Promise.reject(
        new Error('Browser Speech Recognition is unavailable. Use Edge or Chrome, or select GPT Realtime ASR.'),
      )
    }

    this.finalTranscript = ''
    this.interimTranscript = ''
    this.onTranscript = onTranscript
    const recognition = new Recognition()
    this.recognition = recognition
    recognition.lang = language === 'en' ? 'en-AU' : 'zh-CN'
    recognition.continuous = true
    recognition.interimResults = true
    recognition.maxAlternatives = 1

    return new Promise((resolve, reject) => {
      this.resolveStart = resolve
      this.rejectStart = reject
      this.startTimer = window.setTimeout(() => {
        const error = new Error('Browser speech recognition did not start in time.')
        try {
          recognition.abort()
        } catch {
          // Ignore browser abort failures; the Promise still must settle.
        }
        this.rejectStart?.(error)
        this.cleanup()
      }, START_TIMEOUT_MS)

      recognition.onstart = () => {
        if (recognition !== this.recognition) return
        this.clearStartTimer()
        this.active = true
        const resolveStart = this.resolveStart
        this.resolveStart = null
        this.rejectStart = null
        window.dispatchEvent(new CustomEvent('smartoffice:browser-listening-start'))
        resolveStart?.()
      }
      recognition.onresult = (event) => {
        if (recognition !== this.recognition) return
        let finalDelta = ''
        let interim = ''
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index]
          const recognizedText = result?.[0]?.transcript ?? ''
          if (result?.isFinal) finalDelta = joinTranscript(finalDelta, recognizedText)
          else interim = joinTranscript(interim, recognizedText)
        }
        this.finalTranscript = joinTranscript(this.finalTranscript, finalDelta)
        this.interimTranscript = interim
        this.onTranscript?.(joinTranscript(this.finalTranscript, this.interimTranscript))
      }
      recognition.onerror = (event) => {
        if (recognition !== this.recognition) return
        const error = new Error(event.message || `Browser speech recognition failed: ${event.error}`)
        const rejectStart = this.rejectStart
        const rejectStop = this.rejectStop
        this.cleanup()
        rejectStart?.(error)
        rejectStop?.(error)
      }
      recognition.onend = () => {
        if (recognition !== this.recognition) return
        const transcript = joinTranscript(this.finalTranscript, this.interimTranscript)
        const resolveStop = this.resolveStop
        const rejectStart = this.rejectStart
        const started = this.active
        this.cleanup()
        if (!started && rejectStart) {
          rejectStart(new Error('Browser speech recognition ended before it started.'))
        } else {
          resolveStop?.(transcript)
        }
      }

      try {
        recognition.start()
      } catch (error) {
        const normalized = error instanceof Error ? error : new Error(String(error))
        this.cleanup()
        reject(normalized)
      }
    })
  }

  end(): Promise<string> {
    const recognition = this.recognition
    if (!recognition || !this.active) {
      return Promise.reject(new Error('Browser speech capture is not active.'))
    }
    if (this.resolveStop || this.rejectStop) {
      return Promise.reject(new Error('Browser speech capture is already stopping.'))
    }
    return new Promise((resolve, reject) => {
      this.resolveStop = resolve
      this.rejectStop = reject
      this.stopTimer = window.setTimeout(() => {
        const error = new Error('Browser speech recognition did not stop in time.')
        try {
          recognition.abort()
        } catch {
          // Ignore browser abort failures; the Promise still must settle.
        }
        const rejectStop = this.rejectStop
        this.cleanup()
        rejectStop?.(error)
      }, STOP_TIMEOUT_MS)
      try {
        recognition.stop()
      } catch (error) {
        const normalized = error instanceof Error ? error : new Error(String(error))
        this.cleanup()
        reject(normalized)
      }
    })
  }

  abort(): void {
    const recognition = this.recognition
    const rejectStart = this.rejectStart
    const rejectStop = this.rejectStop
    try {
      recognition?.abort()
    } catch {
      // Cleanup and rejection below remain authoritative.
    }
    this.cleanup()
    const error = abortError('Browser speech recognition was aborted.')
    rejectStart?.(error)
    rejectStop?.(error)
  }

  isActive(): boolean {
    return this.active
  }

  private clearStartTimer(): void {
    if (this.startTimer !== null) window.clearTimeout(this.startTimer)
    this.startTimer = null
  }

  private clearStopTimer(): void {
    if (this.stopTimer !== null) window.clearTimeout(this.stopTimer)
    this.stopTimer = null
  }

  private cleanup(): void {
    this.clearStartTimer()
    this.clearStopTimer()
    this.active = false
    this.recognition = null
    this.resolveStart = null
    this.rejectStart = null
    this.resolveStop = null
    this.rejectStop = null
    this.onTranscript = null
    window.dispatchEvent(new CustomEvent('smartoffice:browser-listening-stop'))
  }
}
