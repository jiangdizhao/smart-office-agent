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

export class BrowserSpeechCapture {
  private recognition: BrowserSpeechRecognition | null = null
  private finalTranscript = ''
  private interimTranscript = ''
  private resolveStop: ((transcript: string) => void) | null = null
  private rejectStop: ((error: Error) => void) | null = null
  private onTranscript: TranscriptListener | null = null
  private active = false

  available(): boolean {
    return recognitionConstructor() !== null
  }

  begin(language: VoiceLanguage, onTranscript: TranscriptListener): Promise<void> {
    if (this.active) return Promise.reject(new Error('Browser speech capture is already active.'))
    const Recognition = recognitionConstructor()
    if (!Recognition) {
      return Promise.reject(
        new Error('Browser Speech Recognition is unavailable. Use Edge or Chrome, or select GPT Realtime ASR.'),
      )
    }

    this.finalTranscript = ''
    this.interimTranscript = ''
    this.onTranscript = onTranscript
    this.recognition = new Recognition()
    this.recognition.lang = language === 'en' ? 'en-AU' : 'zh-CN'
    this.recognition.continuous = true
    this.recognition.interimResults = true
    this.recognition.maxAlternatives = 1

    return new Promise((resolve, reject) => {
      const recognition = this.recognition
      if (!recognition) {
        reject(new Error('Browser speech recognizer could not be created.'))
        return
      }

      recognition.onstart = () => {
        this.active = true
        window.dispatchEvent(new CustomEvent('smartoffice:browser-listening-start'))
        resolve()
      }
      recognition.onresult = (event) => {
        let finalDelta = ''
        let interim = ''
        for (let index = 0; index < event.results.length; index += 1) {
          const result = event.results[index]
          const transcript = result?.[0]?.transcript ?? ''
          if (result?.isFinal) finalDelta = joinTranscript(finalDelta, transcript)
          else interim = joinTranscript(interim, transcript)
        }
        this.finalTranscript = joinTranscript(this.finalTranscript, finalDelta)
        this.interimTranscript = interim
        this.onTranscript?.(joinTranscript(this.finalTranscript, this.interimTranscript))
      }
      recognition.onerror = (event) => {
        const error = new Error(event.message || `Browser speech recognition failed: ${event.error}`)
        if (!this.active) reject(error)
        this.rejectStop?.(error)
        this.cleanup()
      }
      recognition.onend = () => {
        const transcript = joinTranscript(this.finalTranscript, this.interimTranscript)
        this.resolveStop?.(transcript)
        this.cleanup()
      }

      try {
        recognition.start()
      } catch (error) {
        this.cleanup()
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  end(): Promise<string> {
    if (!this.recognition || !this.active) {
      return Promise.reject(new Error('Browser speech capture is not active.'))
    }
    return new Promise((resolve, reject) => {
      this.resolveStop = resolve
      this.rejectStop = reject
      this.recognition?.stop()
    })
  }

  abort(): void {
    this.recognition?.abort()
    this.cleanup()
  }

  isActive(): boolean {
    return this.active
  }

  private cleanup(): void {
    this.active = false
    this.recognition = null
    this.resolveStop = null
    this.rejectStop = null
    this.onTranscript = null
    window.dispatchEvent(new CustomEvent('smartoffice:browser-listening-stop'))
  }
}
