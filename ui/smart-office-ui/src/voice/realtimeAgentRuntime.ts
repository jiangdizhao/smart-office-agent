const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

const CONNECTION_TIMEOUT_MS = 12_000
const GET_USER_MEDIA_TIMEOUT_MS = 8_000
const COMMIT_TIMEOUT_MS = 5_000
const TEXT_RESPONSE_TIMEOUT_MS = 25_000
const AUDIO_START_TIMEOUT_MS = 6_000
const AUDIO_COMPLETION_MIN_MS = 8_000
const AUDIO_COMPLETION_MAX_MS = 45_000
const MIC_STABILIZE_MS = 120
const RTP_DRAIN_MS = 180

export type VoiceLanguage = 'zh' | 'en'

export type RealtimeRuntimeStatus = {
  connected: boolean
  connectionState: RTCPeerConnectionState | 'not-created'
  dataChannelState: RTCDataChannelState | 'not-created'
  microphoneAttached: boolean
  responseActive: boolean
  outputActive: boolean
}

export class RealtimeSpeechError extends Error {
  readonly audioStarted: boolean

  constructor(message: string, audioStarted: boolean, name = 'Error') {
    super(message)
    this.name = name
    this.audioStarted = audioStarted
  }
}

type PendingCommit = {
  generation: number
  timer: number
  resolve: () => void
  reject: (error: Error) => void
}

type PendingResponse = {
  requestId: string
  generation: number
  purpose: string
  modalities: Array<'text' | 'audio'>
  text: string
  transcript: string
  responseDone: boolean
  audioStarted: boolean
  audioStopped: boolean
  audioStartedAtMs: number | null
  completionEstimateTextLength: number
  completionTimeoutMs: number
  startTimer: number | null
  completionTimer: number | null
  resolve: (value: string) => void
  reject: (error: Error) => void
}

type RealtimeServerEvent = {
  type?: string
  delta?: string
  text?: string
  transcript?: string
  error?: { code?: string; message?: string }
  response?: {
    status?: string
    metadata?: Record<string, unknown>
    status_details?: { error?: { message?: string } }
  }
}

type AudioContextConstructor = new () => AudioContext

declare global {
  interface Window {
    webkitAudioContext?: AudioContextConstructor
  }
}

function abortError(message: string, audioStarted = false): RealtimeSpeechError {
  return new RealtimeSpeechError(message, audioStarted, 'AbortError')
}

function browserConversationId(): string {
  const key = 'smartoffice_realtime_browser_conversation_id'
  const existing = sessionStorage.getItem(key)
  if (existing) return existing
  const value = `browser-${crypto.randomUUID()}`
  sessionStorage.setItem(key, value)
  return value
}

function estimateAudioCompletionMs(text: string): number {
  const chineseCharacters = (text.match(/[\u3400-\u9fff]/g) ?? []).length
  const englishWords = (text.match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g) ?? []).length
  const punctuation = (text.match(/[，。！？；：,.!?;:]/g) ?? []).length
  const estimate = 4_000 + chineseCharacters * 260 + englishWords * 360 + punctuation * 160
  return Math.max(AUDIO_COMPLETION_MIN_MS, Math.min(AUDIO_COMPLETION_MAX_MS, estimate))
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortError('Operation aborted.'))
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup()
      resolve()
    }, milliseconds)
    const onAbort = () => {
      window.clearTimeout(timer)
      cleanup()
      reject(abortError('Operation aborted.'))
    }
    const cleanup = () => signal?.removeEventListener('abort', onAbort)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

async function fetchWithDeadline(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<Response> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  const abortFromParent = () => controller.abort()
  signal?.addEventListener('abort', abortFromParent, { once: true })
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    window.clearTimeout(timeout)
    signal?.removeEventListener('abort', abortFromParent)
  }
}

function stopStream(stream: MediaStream | null): void {
  for (const track of stream?.getTracks() ?? []) track.stop()
}

export class PersistentRealtimeAgent {
  private pc: RTCPeerConnection | null = null
  private dc: RTCDataChannel | null = null
  private sender: RTCRtpSender | null = null
  private silentContext: AudioContext | null = null
  private silentTrack: MediaStreamTrack | null = null
  private silentStream: MediaStream | null = null
  private microphoneStream: MediaStream | null = null
  private remoteAudio: HTMLAudioElement | null = null
  private remoteOutputStream: MediaStream | null = null
  private connectPromise: Promise<void> | null = null
  private connectAbort: AbortController | null = null
  private generation = 0
  private captureStartedAt = 0
  private captureActive = false
  private pendingCommit: PendingCommit | null = null
  private pendingResponse: PendingResponse | null = null
  private language: VoiceLanguage = 'zh'

  async prewarm(language: VoiceLanguage, signal?: AbortSignal): Promise<void> {
    this.language = language
    const generation = this.generation
    await this.ensureConnected(generation, signal)
    this.assertGeneration(generation)
  }

  async beginCapture(language: VoiceLanguage, signal?: AbortSignal): Promise<void> {
    this.language = language
    const generation = this.generation
    await this.stopOutput()
    await this.ensureConnected(generation, signal)
    this.assertGeneration(generation)
    if (this.captureActive) throw new Error('Realtime microphone capture is already active.')

    const stream = await this.ensureMicrophone(generation, signal)
    this.assertGeneration(generation)
    const track = stream.getAudioTracks()[0] ?? null
    if (!track || !this.sender) throw new Error('No microphone audio track is available.')
    await this.sender.replaceTrack(track)
    await wait(MIC_STABILIZE_MS, signal)
    this.assertGeneration(generation)
    this.send({ type: 'input_audio_buffer.clear' })
    this.captureStartedAt = performance.now()
    this.captureActive = true
    window.dispatchEvent(new CustomEvent('smartoffice:realtime-listening-start'))
  }

  async endCapture(signal?: AbortSignal): Promise<string> {
    const generation = this.generation
    if (!this.captureActive || !this.captureStartedAt) {
      throw new Error('Realtime capture was not started.')
    }
    if (performance.now() - this.captureStartedAt < 250) {
      await this.restoreSilentTrack(false)
      this.captureStartedAt = 0
      this.captureActive = false
      throw new Error('录音时间太短，请看到“正在聆听”后再完整说话。')
    }
    await wait(RTP_DRAIN_MS, signal)
    this.assertGeneration(generation)
    await this.commitAudio(generation, signal)
    await this.restoreSilentTrack(false)
    this.captureActive = false
    const instructions = this.transcriptionInstructions()
    const transcript = await this.createResponse(
      ['text'],
      instructions,
      'speech_understanding',
      instructions,
      generation,
      signal,
    )
    this.assertGeneration(generation)
    this.captureStartedAt = 0
    window.dispatchEvent(new CustomEvent('smartoffice:realtime-listening-stop'))
    return transcript.trim()
  }

  async abortCapture(releaseMicrophone = true): Promise<void> {
    if (this.dc?.readyState === 'open') this.safeSend({ type: 'input_audio_buffer.clear' })
    await this.restoreSilentTrack(releaseMicrophone).catch(() => undefined)
    this.captureStartedAt = 0
    this.captureActive = false
    window.dispatchEvent(new CustomEvent('smartoffice:realtime-listening-stop'))
  }

  currentMicrophoneStream(): MediaStream | null {
    return this.microphoneStream
  }

  async speakExact(
    text: string,
    language: VoiceLanguage,
    signal?: AbortSignal,
  ): Promise<string> {
    const clean = text.trim()
    if (!clean) return ''
    this.language = language
    const generation = this.generation
    await this.stopOutput()
    await this.ensureConnected(generation, signal)
    this.assertGeneration(generation)
    const instruction =
      language === 'en'
        ? `Read the following final answer exactly in a calm, mature and professional virtual-host voice. Do not add, remove, summarize, or paraphrase any word:\n${clean}`
        : `请使用成熟、稳重、亲切、专业的中文虚拟接待员语气，逐字朗读下面的最终答复。不得增加、删除、总结或改写任何内容：\n${clean}`
    const spoken = await this.createResponse(
      ['audio'],
      instruction,
      'exact_backend_answer',
      clean,
      generation,
      signal,
    )
    this.assertGeneration(generation)
    return spoken || clean
  }

  async generateText(
    instructions: string,
    language: VoiceLanguage,
    purpose = 'application_text_generation',
    signal?: AbortSignal,
  ): Promise<string> {
    const clean = instructions.trim()
    if (!clean) return ''
    this.language = language
    const generation = this.generation
    await this.ensureConnected(generation, signal)
    this.assertGeneration(generation)
    return (
      await this.createResponse(
        ['text'],
        clean,
        purpose,
        clean,
        generation,
        signal,
      )
    ).trim()
  }

  currentRemoteAudioStream(): MediaStream | null {
    return this.remoteOutputStream
  }

  async stopOutput(): Promise<void> {
    const pending = this.pendingResponse
    if (pending?.modalities.includes('audio')) {
      this.clearPendingTimers(pending)
      this.pendingResponse = null
      pending.reject(abortError('Realtime speech was interrupted.', pending.audioStarted))
    }
    if (this.dc?.readyState === 'open') {
      this.safeSend({ type: 'response.cancel' })
      this.safeSend({ type: 'output_audio_buffer.clear' })
    }
    this.remoteAudio?.pause()
    window.dispatchEvent(new CustomEvent('smartoffice:realtime-speaking-stop'))
  }

  status(): RealtimeRuntimeStatus {
    const pending = this.pendingResponse
    return {
      connected: this.pc?.connectionState === 'connected',
      connectionState: this.pc?.connectionState ?? 'not-created',
      dataChannelState: this.dc?.readyState ?? 'not-created',
      microphoneAttached: Boolean(this.microphoneStream && this.captureActive),
      responseActive: pending !== null,
      outputActive: Boolean(
        pending?.modalities.includes('audio') && pending.audioStarted && !pending.audioStopped,
      ),
    }
  }

  async shutdown(): Promise<void> {
    this.generation += 1
    this.connectAbort?.abort()
    this.connectAbort = null
    this.connectPromise = null
    this.rejectPending(abortError('GPT Realtime session was revoked.'))

    const microphone = this.microphoneStream
    this.microphoneStream = null
    stopStream(microphone)
    this.captureStartedAt = 0
    this.captureActive = false
    this.closeConnectionObjects()

    const silentTrack = this.silentTrack
    const silentContext = this.silentContext
    this.silentTrack = null
    this.silentStream = null
    this.silentContext = null
    silentTrack?.stop()
    await silentContext?.close().catch(() => undefined)
    window.dispatchEvent(new CustomEvent('smartoffice:realtime-visit-session-closed'))
  }

  transcriptionInstructions(): string {
    return `
You are a multilingual speech transcription and correction layer, not a conversational assistant.
Return only the user's final intended utterance as normalized plain text. Do not answer the user.
Detect the language from the spoken audio itself and never translate it.
Preserve genuine Chinese-English code-switching.
Later explicit corrections override earlier uncertain words.
Chinese correction signals such as “不”, “不是”, “不对”, “我是说”, “应该是” and character explanations are authoritative.
English correction signals such as “no”, “not that”, “I mean”, “correction”, and “it should be” are authoritative.
Relevant terms include Microsoft Teams, PowerPoint, Word, Excel, Outlook, OneNote, meeting, presentation, mute, camera, next slide, previous slide, and screen sharing.
Never invent a request. If genuinely unintelligible, output exactly __UNCLEAR__.
Output only normalized plain text without labels, JSON, Markdown, quotation marks, explanations, or translations.
`.trim()
  }

  private assertGeneration(expected: number): void {
    if (expected !== this.generation) throw abortError('Stale GPT Realtime operation was fenced.')
  }

  private async ensureConnected(generation: number, signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) throw abortError('Realtime connection was aborted.')
    this.assertGeneration(generation)
    if (
      this.pc &&
      this.dc?.readyState === 'open' &&
      ['connected', 'connecting', 'new'].includes(this.pc.connectionState)
    ) {
      if (this.remoteAudio?.paused) await this.remoteAudio.play().catch(() => undefined)
      return
    }
    if (this.connectPromise) {
      await this.connectPromise
      this.assertGeneration(generation)
      return
    }
    const connectAbort = new AbortController()
    const abortFromParent = () => connectAbort.abort()
    signal?.addEventListener('abort', abortFromParent, { once: true })
    this.connectAbort = connectAbort
    const promise = this.connect(generation, connectAbort.signal)
    this.connectPromise = promise
    try {
      await promise
      this.assertGeneration(generation)
    } finally {
      signal?.removeEventListener('abort', abortFromParent)
      if (this.connectPromise === promise) this.connectPromise = null
      if (this.connectAbort === connectAbort) this.connectAbort = null
    }
  }

  private async connect(generation: number, signal: AbortSignal): Promise<void> {
    this.closeConnectionObjects()
    const statusResponse = await fetchWithDeadline(
      `${API_BASE_URL}/api/realtime/status`,
      { headers: { Accept: 'application/json' } },
      CONNECTION_TIMEOUT_MS,
      signal,
    )
    this.assertGeneration(generation)
    if (!statusResponse.ok) throw new Error(`Realtime status failed: ${statusResponse.status}`)
    const status = (await statusResponse.json()) as { configured?: boolean; enabled?: boolean }
    if (!status.configured || !status.enabled) {
      throw new Error('GPT Realtime is not configured in the Backend process.')
    }

    this.createSilentTrack()
    if (!this.silentTrack || !this.silentStream) {
      throw new Error('Could not create a silent WebRTC track.')
    }

    const pc = new RTCPeerConnection()
    const dc = pc.createDataChannel('oai-events')
    const remoteAudio = document.createElement('audio')
    remoteAudio.autoplay = true
    remoteAudio.playsInline = true
    remoteAudio.hidden = true
    remoteAudio.dataset.owner = 'smart-office-realtime'
    document.body.appendChild(remoteAudio)

    pc.addEventListener('track', (event) => {
      if (generation !== this.generation || pc !== this.pc) return
      const stream = event.streams[0] ?? new MediaStream([event.track])
      this.remoteOutputStream = stream
      remoteAudio.srcObject = stream
      window.dispatchEvent(
        new CustomEvent<MediaStream>('smartoffice:realtime-remote-stream', { detail: stream }),
      )
      void remoteAudio.play().catch(() => undefined)
    })
    dc.addEventListener('message', (event: MessageEvent<string>) => {
      if (generation === this.generation && dc === this.dc) this.handleServerEvent(event)
    })
    pc.addEventListener('connectionstatechange', () => {
      if (pc !== this.pc) return
      window.dispatchEvent(
        new CustomEvent('smartoffice:realtime-connection-state', { detail: pc.connectionState }),
      )
      if (['failed', 'closed'].includes(pc.connectionState)) {
        this.rejectPending(new Error('GPT Realtime WebRTC connection was lost.'))
      }
    })

    this.pc = pc
    this.dc = dc
    this.remoteAudio = remoteAudio
    this.sender = pc.addTrack(this.silentTrack, this.silentStream)

    try {
      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      const sdp = pc.localDescription?.sdp ?? offer.sdp
      if (!sdp) throw new Error('Could not create a WebRTC SDP offer.')
      const sessionResponse = await fetchWithDeadline(
        `${API_BASE_URL}/api/realtime/session?conversation_id=${encodeURIComponent(browserConversationId())}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/sdp' },
          body: sdp,
        },
        CONNECTION_TIMEOUT_MS,
        signal,
      )
      this.assertGeneration(generation)
      if (!sessionResponse.ok) {
        const detail = await sessionResponse.text().catch(() => '')
        throw new Error(`Realtime session failed: ${sessionResponse.status} ${detail}`)
      }
      await pc.setRemoteDescription({ type: 'answer', sdp: await sessionResponse.text() })
      await this.waitForDataChannel(dc, signal)
      this.assertGeneration(generation)
      this.send({
        type: 'session.update',
        session: {
          type: 'realtime',
          output_modalities: ['audio'],
          instructions:
            this.language === 'en'
              ? 'You are the voice layer for a Smart Office virtual host. Speak only text explicitly supplied by the application. Never invent facts or claim an office action succeeded.'
              : '你是 Smart Office 虚拟接待员的语音层。只朗读应用明确提供的文字；不得编造事实，也不得声称办公操作已经成功。',
          audio: {
            input: { turn_detection: null },
            output: {
              voice: import.meta.env.VITE_REALTIME_VOICE ?? 'marin',
              speed: 1.0,
            },
          },
        },
      })
      window.dispatchEvent(new CustomEvent('smartoffice:realtime-connected'))
    } catch (error) {
      if (pc === this.pc) this.closeConnectionObjects()
      throw error
    }
  }

  private createSilentTrack(): void {
    if (this.silentTrack && this.silentStream) return
    const AudioContextClass = window.AudioContext ?? window.webkitAudioContext
    if (!AudioContextClass) throw new Error('Web Audio is unavailable.')
    const context = new AudioContextClass()
    const destination = context.createMediaStreamDestination()
    const oscillator = context.createOscillator()
    const gain = context.createGain()
    gain.gain.value = 0
    oscillator.connect(gain)
    gain.connect(destination)
    oscillator.start()
    this.silentContext = context
    this.silentStream = destination.stream
    this.silentTrack = destination.stream.getAudioTracks()[0] ?? null
  }

  private async ensureMicrophone(
    generation: number,
    signal?: AbortSignal,
  ): Promise<MediaStream> {
    if (signal?.aborted) throw abortError('Microphone acquisition was aborted.')
    if (this.microphoneStream?.getAudioTracks().some((track) => track.readyState === 'live')) {
      return this.microphoneStream
    }

    let abandoned = false
    const request = navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    })
    const timeout = new Promise<never>((_resolve, reject) => {
      const timer = window.setTimeout(() => {
        abandoned = true
        reject(new Error('Microphone acquisition timed out.'))
      }, GET_USER_MEDIA_TIMEOUT_MS)
      const onAbort = () => {
        abandoned = true
        window.clearTimeout(timer)
        reject(abortError('Microphone acquisition was aborted.'))
      }
      signal?.addEventListener('abort', onAbort, { once: true })
      void request.finally(() => {
        window.clearTimeout(timer)
        signal?.removeEventListener('abort', onAbort)
      })
    })
    request.then((stream) => {
      if (abandoned || generation !== this.generation) stopStream(stream)
    }).catch(() => undefined)
    const stream = await Promise.race([request, timeout])
    this.assertGeneration(generation)
    this.microphoneStream = stream
    return stream
  }

  private async restoreSilentTrack(releaseMicrophone: boolean): Promise<void> {
    if (this.sender && this.silentTrack) await this.sender.replaceTrack(this.silentTrack)
    if (releaseMicrophone) {
      stopStream(this.microphoneStream)
      this.microphoneStream = null
    }
  }

  private commitAudio(generation: number, signal?: AbortSignal): Promise<void> {
    if (this.pendingCommit) return Promise.reject(new Error('Audio commit is already active.'))
    return new Promise((resolve, reject) => {
      const onAbort = () => finish(abortError('Audio commit was aborted.'))
      const finish = (error?: Error) => {
        signal?.removeEventListener('abort', onAbort)
        const pending = this.pendingCommit
        if (pending?.generation === generation) {
          window.clearTimeout(pending.timer)
          this.pendingCommit = null
        }
        if (error) reject(error)
        else resolve()
      }
      const timer = window.setTimeout(
        () => finish(new Error('GPT Realtime did not confirm the audio buffer.')),
        COMMIT_TIMEOUT_MS,
      )
      this.pendingCommit = {
        generation,
        timer,
        resolve: () => finish(),
        reject: (error) => finish(error),
      }
      signal?.addEventListener('abort', onAbort, { once: true })
      this.send({ type: 'input_audio_buffer.commit' })
    })
  }

  private createResponse(
    modalities: Array<'text' | 'audio'>,
    instructions: string,
    purpose: string,
    completionEstimateText: string,
    generation: number,
    signal?: AbortSignal,
  ): Promise<string> {
    if (this.pendingResponse) {
      return Promise.reject(new Error('Another GPT Realtime response is still active.'))
    }
    return new Promise((resolve, reject) => {
      const requestId = `${purpose}-${generation}-${Date.now()}-${Math.random().toString(16).slice(2)}`
      const audio = modalities.includes('audio')
      const completionTimeoutMs = audio
        ? estimateAudioCompletionMs(completionEstimateText)
        : TEXT_RESPONSE_TIMEOUT_MS
      const pending: PendingResponse = {
        requestId,
        generation,
        purpose,
        modalities,
        text: '',
        transcript: '',
        responseDone: false,
        audioStarted: false,
        audioStopped: !audio,
        audioStartedAtMs: null,
        completionEstimateTextLength: completionEstimateText.length,
        completionTimeoutMs,
        startTimer: null,
        completionTimer: null,
        resolve,
        reject,
      }
      console.info('[RealtimeDiagnostics] response-created', {
        requestId,
        purpose,
        generation,
        modalities,
        completionEstimateTextLength: pending.completionEstimateTextLength,
        completionTimeoutMs: pending.completionTimeoutMs,
      })
      const onAbort = () => {
        if (this.pendingResponse?.requestId !== requestId) return
        this.safeSend({ type: 'response.cancel' })
        this.safeSend({ type: 'output_audio_buffer.clear' })
        this.finishResponseError(pending, abortError('Realtime response was aborted.', pending.audioStarted))
      }
      signal?.addEventListener('abort', onAbort, { once: true })
      const wrappedResolve = pending.resolve
      const wrappedReject = pending.reject
      pending.resolve = (value) => {
        signal?.removeEventListener('abort', onAbort)
        wrappedResolve(value)
      }
      pending.reject = (error) => {
        signal?.removeEventListener('abort', onAbort)
        wrappedReject(error)
      }

      if (audio) {
        pending.startTimer = window.setTimeout(() => {
          if (this.pendingResponse?.requestId !== requestId) return
          this.safeSend({ type: 'response.cancel' })
          console.error('[RealtimeDiagnostics] audio-start-timeout', {
            requestId,
            purpose,
            generation,
            completionEstimateTextLength: pending.completionEstimateTextLength,
            startTimeoutMs: AUDIO_START_TIMEOUT_MS,
          })
          this.finishResponseError(
            pending,
            new RealtimeSpeechError('GPT Realtime audio did not start in time.', false),
          )
        }, AUDIO_START_TIMEOUT_MS)
      } else {
        pending.completionTimer = window.setTimeout(() => {
          if (this.pendingResponse?.requestId !== requestId) return
          this.safeSend({ type: 'response.cancel' })
          this.finishResponseError(pending, new Error('GPT Realtime text response timed out.'))
        }, TEXT_RESPONSE_TIMEOUT_MS)
      }

      this.pendingResponse = pending
      this.send({
        type: 'response.create',
        response: {
          conversation: 'none',
          output_modalities: modalities,
          metadata: {
            purpose: String(purpose),
            request_id: String(requestId),
            generation: String(generation),
            completion_timeout_ms: String(pending.completionTimeoutMs),
          },
          instructions,
        },
      })
    })
  }

  private startAudioCompletionTimer(pending: PendingResponse): void {
    if (pending.completionTimer !== null) return
    pending.audioStartedAtMs = performance.now()
    console.info('[RealtimeDiagnostics] audio-completion-timer-started', {
      requestId: pending.requestId,
      purpose: pending.purpose,
      generation: pending.generation,
      completionEstimateTextLength: pending.completionEstimateTextLength,
      completionTimeoutMs: pending.completionTimeoutMs,
    })
    pending.completionTimer = window.setTimeout(() => {
      if (this.pendingResponse?.requestId !== pending.requestId) return
      const elapsedMs = Math.round(
        performance.now() - (pending.audioStartedAtMs ?? performance.now()),
      )
      console.error('[RealtimeDiagnostics] audio-completion-timeout', {
        requestId: pending.requestId,
        purpose: pending.purpose,
        generation: pending.generation,
        completionEstimateTextLength: pending.completionEstimateTextLength,
        completionTimeoutMs: pending.completionTimeoutMs,
        elapsedMs,
        transcriptLength: pending.transcript.length,
        textLength: pending.text.length,
        responseDone: pending.responseDone,
      })
      this.safeSend({ type: 'response.cancel' })
      this.safeSend({ type: 'output_audio_buffer.clear' })
      this.finishResponseError(
        pending,
        new RealtimeSpeechError(
          'GPT Realtime audio did not report completion in time.',
          pending.audioStarted,
        ),
      )
    }, pending.completionTimeoutMs)
  }

  private resolveResponse(pending: PendingResponse): void {
    if (this.pendingResponse?.requestId !== pending.requestId) return
    const elapsedMs = pending.audioStartedAtMs === null
      ? null
      : Math.round(performance.now() - pending.audioStartedAtMs)
    console.info('[RealtimeDiagnostics] response-resolved', {
      requestId: pending.requestId,
      purpose: pending.purpose,
      generation: pending.generation,
      audioStarted: pending.audioStarted,
      audioStopped: pending.audioStopped,
      responseDone: pending.responseDone,
      elapsedMs,
      completionTimeoutMs: pending.completionTimeoutMs,
      transcriptLength: pending.transcript.length,
      textLength: pending.text.length,
    })
    this.clearPendingTimers(pending)
    this.pendingResponse = null
    pending.resolve((pending.transcript || pending.text).trim())
  }

  private finishResponseError(pending: PendingResponse, error: Error): void {
    if (this.pendingResponse?.requestId !== pending.requestId) return
    this.clearPendingTimers(pending)
    this.pendingResponse = null
    pending.reject(error)
  }

  private clearPendingTimers(pending: PendingResponse): void {
    if (pending.startTimer !== null) window.clearTimeout(pending.startTimer)
    if (pending.completionTimer !== null) window.clearTimeout(pending.completionTimer)
    pending.startTimer = null
    pending.completionTimer = null
  }

  private handleServerEvent(message: MessageEvent<string>): void {
    let event: RealtimeServerEvent
    try {
      event = JSON.parse(message.data) as RealtimeServerEvent
    } catch {
      return
    }

    if (event.type === 'input_audio_buffer.committed') {
      this.pendingCommit?.resolve()
      return
    }
    const pending = this.pendingResponse
    if (event.type === 'response.output_text.delta' && pending) {
      pending.text += event.delta ?? ''
      return
    }
    if (event.type === 'response.output_text.done' && pending) {
      pending.text = event.text ?? pending.text
      return
    }
    if (event.type === 'response.output_audio_transcript.delta' && pending) {
      pending.transcript += event.delta ?? ''
      return
    }
    if (event.type === 'response.output_audio_transcript.done' && pending) {
      pending.transcript = event.transcript ?? pending.transcript
      return
    }
    if (event.type === 'output_audio_buffer.started') {
      if (pending?.modalities.includes('audio')) {
        pending.audioStarted = true
        if (pending.startTimer !== null) {
          window.clearTimeout(pending.startTimer)
          pending.startTimer = null
        }
        this.startAudioCompletionTimer(pending)
      }
      window.dispatchEvent(new CustomEvent('smartoffice:realtime-speaking-start'))
      return
    }
    if (event.type === 'output_audio_buffer.stopped') {
      if (pending?.modalities.includes('audio') && pending.audioStarted) {
        pending.audioStopped = true
        this.resolveResponse(pending)
      }
      window.dispatchEvent(new CustomEvent('smartoffice:realtime-speaking-stop'))
      return
    }
    if (event.type === 'response.done') {
      if (!pending) return
      const responseRequestId = event.response?.metadata?.request_id
      if (typeof responseRequestId === 'string' && responseRequestId !== pending.requestId) return
      if (event.response?.status === 'failed') {
        const detail =
          event.response.status_details?.error?.message ?? 'GPT Realtime response failed.'
        this.finishResponseError(
          pending,
          pending.modalities.includes('audio')
            ? new RealtimeSpeechError(detail, pending.audioStarted)
            : new Error(detail),
        )
        return
      }
      pending.responseDone = true
      if (!pending.modalities.includes('audio')) this.resolveResponse(pending)
      return
    }
    if (event.type === 'error') {
      const code = event.error?.code ?? ''
      if (['response_cancel_not_active', 'input_audio_buffer_clear_empty'].includes(code)) return
      const error = new Error(event.error?.message ?? 'GPT Realtime returned an unknown error.')
      if (this.pendingCommit) this.pendingCommit.reject(error)
      else if (pending) {
        this.finishResponseError(
          pending,
          pending.modalities.includes('audio')
            ? new RealtimeSpeechError(error.message, pending.audioStarted)
            : error,
        )
      }
    }
  }

  private send(event: Record<string, unknown>): void {
    if (this.dc?.readyState !== 'open') {
      throw new Error('GPT Realtime data channel is not open.')
    }
    this.dc.send(JSON.stringify(event))
  }

  private safeSend(event: Record<string, unknown>): void {
    if (this.dc?.readyState === 'open') this.dc.send(JSON.stringify(event))
  }

  private waitForDataChannel(channel: RTCDataChannel, signal?: AbortSignal): Promise<void> {
    if (channel.readyState === 'open') return Promise.resolve()
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        cleanup()
        reject(new Error('Timed out while opening GPT Realtime data channel.'))
      }, CONNECTION_TIMEOUT_MS)
      const cleanup = () => {
        window.clearTimeout(timer)
        channel.removeEventListener('open', onOpen)
        channel.removeEventListener('error', onError)
        signal?.removeEventListener('abort', onAbort)
      }
      const onOpen = () => {
        cleanup()
        resolve()
      }
      const onError = () => {
        cleanup()
        reject(new Error('Could not open GPT Realtime data channel.'))
      }
      const onAbort = () => {
        cleanup()
        reject(abortError('Realtime connection was aborted.'))
      }
      channel.addEventListener('open', onOpen)
      channel.addEventListener('error', onError)
      signal?.addEventListener('abort', onAbort, { once: true })
    })
  }

  private rejectPending(error: Error): void {
    this.pendingCommit?.reject(error)
    const pending = this.pendingResponse
    if (pending) {
      this.clearPendingTimers(pending)
      this.pendingResponse = null
      pending.reject(
        pending.modalities.includes('audio')
          ? new RealtimeSpeechError(error.message, pending.audioStarted, error.name)
          : error,
      )
    }
  }

  private closeConnectionObjects(): void {
    this.dc?.close()
    this.pc?.close()
    this.remoteAudio?.pause()
    this.remoteAudio?.remove()
    this.dc = null
    this.pc = null
    this.sender = null
    this.remoteAudio = null
    this.remoteOutputStream = null
  }
}

export const realtimeAgent = new PersistentRealtimeAgent()
