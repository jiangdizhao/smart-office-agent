const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

const CONNECTION_TIMEOUT_MS = 15_000
const COMMIT_TIMEOUT_MS = 5_000
const TEXT_RESPONSE_TIMEOUT_MS = 30_000
const AUDIO_START_TIMEOUT_MS = 15_000
const AUDIO_COMPLETION_MIN_MS = 90_000
const AUDIO_COMPLETION_MAX_MS = 360_000
const MIC_STABILIZE_MS = 160
const RTP_DRAIN_MS = 220

export type VoiceLanguage = 'zh' | 'en'

export type RealtimeRuntimeStatus = {
  connected: boolean
  connectionState: RTCPeerConnectionState | 'not-created'
  dataChannelState: RTCDataChannelState | 'not-created'
  microphoneAttached: boolean
  responseActive: boolean
  outputActive: boolean
}

type PendingCommit = {
  resolve: () => void
  reject: (error: Error) => void
}

type PendingResponse = {
  requestId: string
  purpose: string
  modalities: Array<'text' | 'audio'>
  text: string
  transcript: string
  responseDone: boolean
  audioStarted: boolean
  audioStopped: boolean
  startedAt: number
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

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function abortError(message: string): Error {
  const error = new Error(message)
  error.name = 'AbortError'
  return error
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
  const estimate =
    35_000 + chineseCharacters * 320 + englishWords * 420 + punctuation * 250
  return Math.max(
    AUDIO_COMPLETION_MIN_MS,
    Math.min(AUDIO_COMPLETION_MAX_MS, estimate),
  )
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
  private connectPromise: Promise<void> | null = null
  private captureStartedAt = 0
  private pendingCommit: PendingCommit | null = null
  private pendingResponse: PendingResponse | null = null
  private operationQueue: Promise<unknown> = Promise.resolve()
  private language: VoiceLanguage = 'zh'

  async prewarm(language: VoiceLanguage): Promise<void> {
    this.language = language
    await this.ensureConnected()
  }

  async beginCapture(language: VoiceLanguage): Promise<void> {
    this.language = language
    await this.enqueue(async () => {
      await this.stopOutput()
      await this.ensureConnected()
      this.microphoneStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      })
      const track = this.microphoneStream.getAudioTracks()[0] ?? null
      if (!track || !this.sender) {
        await this.releaseMicrophone()
        throw new Error('No microphone audio track is available.')
      }
      await this.sender.replaceTrack(track)
      await wait(MIC_STABILIZE_MS)
      this.send({ type: 'input_audio_buffer.clear' })
      this.captureStartedAt = performance.now()
      window.dispatchEvent(new CustomEvent('smartoffice:realtime-listening-start'))
    })
  }

  async endCapture(): Promise<string> {
    return await this.enqueue(async () => {
      if (!this.captureStartedAt) throw new Error('Realtime capture was not started.')
      if (performance.now() - this.captureStartedAt < 250) {
        await this.restoreSilentTrack()
        this.captureStartedAt = 0
        throw new Error('录音时间太短，请看到“正在聆听”后再完整说话。')
      }
      await wait(RTP_DRAIN_MS)
      await this.commitAudio()
      await this.restoreSilentTrack()
      const transcript = await this.createResponse(
        ['text'],
        this.transcriptionInstructions(),
        'speech_understanding',
      )
      this.captureStartedAt = 0
      window.dispatchEvent(new CustomEvent('smartoffice:realtime-listening-stop'))
      return transcript.trim()
    })
  }

  async abortCapture(): Promise<void> {
    if (this.dc?.readyState === 'open') this.safeSend({ type: 'input_audio_buffer.clear' })
    await this.restoreSilentTrack()
    this.captureStartedAt = 0
    window.dispatchEvent(new CustomEvent('smartoffice:realtime-listening-stop'))
  }

  async speakExact(text: string, language: VoiceLanguage): Promise<string> {
    const clean = text.trim()
    if (!clean) return ''
    this.language = language
    return await this.enqueue(async () => {
      await this.ensureConnected()
      const instruction =
        language === 'en'
          ? `Read the following final answer exactly in a calm, mature and professional virtual-host voice. Do not add, remove, summarize, or paraphrase any word:\n${clean}`
          : `请使用成熟、稳重、亲切、专业的中文虚拟接待员语气，逐字朗读下面的最终答复。不得增加、删除、总结或改写任何内容：\n${clean}`
      const spoken = await this.createResponse(
        ['audio'],
        instruction,
        'exact_backend_answer',
        clean,
      )
      return spoken || clean
    })
  }

  async stopOutput(): Promise<void> {
    const pending = this.pendingResponse
    if (pending?.modalities.includes('audio')) {
      this.clearPendingTimers(pending)
      this.pendingResponse = null
      pending.reject(abortError('Realtime speech was interrupted.'))
    }
    if (this.dc?.readyState === 'open') {
      this.safeSend({ type: 'response.cancel' })
      this.safeSend({ type: 'output_audio_buffer.clear' })
    }
    this.remoteAudio?.pause()
    window.dispatchEvent(new CustomEvent('smartoffice:realtime-speaking-stop'))
  }

  status(): RealtimeRuntimeStatus {
    return {
      connected: this.pc?.connectionState === 'connected',
      connectionState: this.pc?.connectionState ?? 'not-created',
      dataChannelState: this.dc?.readyState ?? 'not-created',
      microphoneAttached: this.microphoneStream !== null,
      responseActive: this.pendingResponse !== null,
      outputActive: Boolean(
        this.pendingResponse?.modalities.includes('audio') &&
          this.pendingResponse.audioStarted &&
          !this.pendingResponse.audioStopped,
      ),
    }
  }

  async shutdown(): Promise<void> {
    await this.shutdownConnection()
    this.silentTrack?.stop()
    this.silentTrack = null
    this.silentStream = null
    await this.silentContext?.close().catch(() => undefined)
    this.silentContext = null
  }

  private async enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.operationQueue.then(operation, operation)
    this.operationQueue = next.catch(() => undefined)
    return await next
  }

  private async ensureConnected(): Promise<void> {
    if (
      this.pc &&
      this.dc?.readyState === 'open' &&
      ['connected', 'connecting', 'new'].includes(this.pc.connectionState)
    ) {
      if (this.remoteAudio?.paused) {
        await this.remoteAudio.play().catch(() => undefined)
      }
      return
    }
    if (this.connectPromise) return await this.connectPromise
    this.connectPromise = this.connect()
    try {
      await this.connectPromise
    } finally {
      this.connectPromise = null
    }
  }

  private async connect(): Promise<void> {
    await this.shutdownConnection()

    const statusResponse = await fetch(`${API_BASE_URL}/api/realtime/status`, {
      headers: { Accept: 'application/json' },
    })
    if (!statusResponse.ok) {
      throw new Error(`Realtime status failed: ${statusResponse.status}`)
    }
    const status = (await statusResponse.json()) as {
      configured?: boolean
      enabled?: boolean
      model?: string
    }
    if (!status.configured || !status.enabled) {
      throw new Error('GPT Realtime is not configured in the Backend process.')
    }

    this.createSilentTrack()
    if (!this.silentTrack || !this.silentStream) {
      throw new Error('Could not create a silent WebRTC track.')
    }

    const pc = new RTCPeerConnection()
    const dc = pc.createDataChannel('oai-events')
    this.pc = pc
    this.dc = dc
    this.sender = pc.addTrack(this.silentTrack, this.silentStream)

    const remoteAudio = document.createElement('audio')
    remoteAudio.autoplay = true
    remoteAudio.playsInline = true
    remoteAudio.hidden = true
    remoteAudio.dataset.owner = 'smart-office-realtime'
    document.body.appendChild(remoteAudio)
    this.remoteAudio = remoteAudio

    pc.addEventListener('track', (event) => {
      remoteAudio.srcObject = event.streams[0] ?? new MediaStream([event.track])
      void remoteAudio.play().catch(() => undefined)
    })
    dc.addEventListener('message', (event: MessageEvent<string>) => {
      this.handleServerEvent(event)
    })
    pc.addEventListener('connectionstatechange', () => {
      window.dispatchEvent(
        new CustomEvent('smartoffice:realtime-connection-state', {
          detail: pc.connectionState,
        }),
      )
      if (['failed', 'closed'].includes(pc.connectionState)) {
        this.rejectPending(new Error('GPT Realtime WebRTC connection was lost.'))
      }
    })

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    const sdp = pc.localDescription?.sdp ?? offer.sdp
    if (!sdp) throw new Error('Could not create a WebRTC SDP offer.')

    const sessionResponse = await fetch(
      `${API_BASE_URL}/api/realtime/session?conversation_id=${encodeURIComponent(browserConversationId())}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: sdp,
      },
    )
    if (!sessionResponse.ok) {
      const detail = await sessionResponse.text().catch(() => '')
      throw new Error(`Realtime session failed: ${sessionResponse.status} ${detail}`)
    }

    await pc.setRemoteDescription({ type: 'answer', sdp: await sessionResponse.text() })
    await this.waitForDataChannel(dc)
    this.send({
      type: 'session.update',
      session: {
        type: 'realtime',
        output_modalities: ['audio'],
        instructions:
          this.language === 'en'
            ? 'You are the voice layer for a Smart Office virtual host. Speak only text explicitly supplied by the application. Never invent company facts or claim that an office action succeeded.'
            : '你是 Smart Office 虚拟接待员的语音层。只朗读应用明确提供的文字；不得编造公司事实，也不得声称某个办公操作已经成功。',
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

  private async releaseMicrophone(): Promise<void> {
    for (const track of this.microphoneStream?.getTracks() ?? []) track.stop()
    this.microphoneStream = null
  }

  private async restoreSilentTrack(): Promise<void> {
    if (this.sender && this.silentTrack) await this.sender.replaceTrack(this.silentTrack)
    await this.releaseMicrophone()
  }

  private commitAudio(): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (this.pendingCommit) this.pendingCommit = null
        reject(new Error('GPT Realtime did not confirm the audio buffer.'))
      }, COMMIT_TIMEOUT_MS)
      this.pendingCommit = {
        resolve: () => {
          window.clearTimeout(timer)
          this.pendingCommit = null
          resolve()
        },
        reject: (error) => {
          window.clearTimeout(timer)
          this.pendingCommit = null
          reject(error)
        },
      }
      this.send({ type: 'input_audio_buffer.commit' })
    })
  }

  private createResponse(
    modalities: Array<'text' | 'audio'>,
    instructions: string,
    purpose: string,
    completionEstimateText = instructions,
  ): Promise<string> {
    return new Promise((resolve, reject) => {
      if (this.pendingResponse) {
        reject(new Error('Another GPT Realtime response is still active.'))
        return
      }

      const requestId = `${purpose}-${Date.now()}-${Math.random().toString(16).slice(2)}`
      const audio = modalities.includes('audio')
      const pending: PendingResponse = {
        requestId,
        purpose,
        modalities,
        text: '',
        transcript: '',
        responseDone: false,
        audioStarted: false,
        audioStopped: !audio,
        startedAt: performance.now(),
        startTimer: null,
        completionTimer: null,
        resolve,
        reject,
      }

      if (audio) {
        pending.startTimer = window.setTimeout(() => {
          if (this.pendingResponse?.requestId !== requestId) return
          this.safeSend({ type: 'response.cancel' })
          this.pendingResponse = null
          reject(new Error('GPT Realtime audio did not start within 15 seconds.'))
        }, AUDIO_START_TIMEOUT_MS)
      } else {
        pending.completionTimer = window.setTimeout(() => {
          if (this.pendingResponse?.requestId !== requestId) return
          this.safeSend({ type: 'response.cancel' })
          this.pendingResponse = null
          reject(new Error('GPT Realtime text response timed out.'))
        }, TEXT_RESPONSE_TIMEOUT_MS)
      }

      this.pendingResponse = pending
      this.send({
        type: 'response.create',
        response: {
          conversation: 'none',
          output_modalities: modalities,
          metadata: {
            purpose,
            request_id: requestId,
            completion_timeout_ms: audio
              ? estimateAudioCompletionMs(completionEstimateText)
              : TEXT_RESPONSE_TIMEOUT_MS,
          },
          instructions,
        },
      })
    })
  }

  private startAudioCompletionTimer(pending: PendingResponse): void {
    if (pending.completionTimer !== null) return
    pending.completionTimer = window.setTimeout(() => {
      if (this.pendingResponse?.requestId !== pending.requestId) return
      this.safeSend({ type: 'response.cancel' })
      this.safeSend({ type: 'output_audio_buffer.clear' })
      this.pendingResponse = null
      pending.reject(new Error('GPT Realtime audio completion timed out.'))
    }, estimateAudioCompletionMs(pending.transcript || pending.text || pending.purpose))
  }

  private maybeResolveResponse(): void {
    const pending = this.pendingResponse
    if (!pending || !pending.responseDone || !pending.audioStopped) return
    this.clearPendingTimers(pending)
    this.pendingResponse = null
    const text = (pending.transcript || pending.text).trim()
    pending.resolve(text)
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
    if (event.type === 'response.output_text.delta' && this.pendingResponse) {
      this.pendingResponse.text += event.delta ?? ''
      return
    }
    if (event.type === 'response.output_text.done' && this.pendingResponse) {
      this.pendingResponse.text = event.text ?? this.pendingResponse.text
      return
    }
    if (event.type === 'response.output_audio_transcript.delta' && this.pendingResponse) {
      this.pendingResponse.transcript += event.delta ?? ''
      return
    }
    if (event.type === 'response.output_audio_transcript.done' && this.pendingResponse) {
      this.pendingResponse.transcript = event.transcript ?? this.pendingResponse.transcript
      return
    }
    if (event.type === 'output_audio_buffer.started') {
      const pending = this.pendingResponse
      if (pending) {
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
      if (this.pendingResponse) this.pendingResponse.audioStopped = true
      window.dispatchEvent(new CustomEvent('smartoffice:realtime-speaking-stop'))
      this.maybeResolveResponse()
      return
    }
    if (event.type === 'response.done') {
      const pending = this.pendingResponse
      if (!pending) return
      const responseRequestId = event.response?.metadata?.request_id
      if (typeof responseRequestId === 'string' && responseRequestId !== pending.requestId) {
        return
      }
      if (event.response?.status === 'failed') {
        const detail =
          event.response.status_details?.error?.message ?? 'GPT Realtime response failed.'
        this.clearPendingTimers(pending)
        this.pendingResponse = null
        pending.reject(new Error(detail))
        return
      }
      pending.responseDone = true
      this.maybeResolveResponse()
      return
    }
    if (event.type === 'error') {
      const code = event.error?.code ?? ''
      if (['response_cancel_not_active', 'input_audio_buffer_clear_empty'].includes(code)) {
        return
      }
      const error = new Error(event.error?.message ?? 'GPT Realtime returned an unknown error.')
      if (this.pendingCommit) {
        this.pendingCommit.reject(error)
      } else if (this.pendingResponse) {
        const pending = this.pendingResponse
        this.clearPendingTimers(pending)
        this.pendingResponse = null
        pending.reject(error)
      }
    }
  }

  private transcriptionInstructions(): string {
    return `
You are a speech-understanding layer, not a conversational assistant.
Return only the user's final intended utterance as plain text. Do not answer.
Language: ${this.language === 'en' ? 'English' : 'Chinese Mandarin'}.
Later explicit corrections override earlier uncertain words.
Chinese correction signals such as “不”, “不是”, “不对”, “我是说”, “应该是” and character explanations are authoritative.
Example: “打开钱会色的演示，不对，是浅灰色，深浅的浅，灰色的灰” becomes “打开浅灰色的演示”.
Relevant terms include Microsoft Teams, PowerPoint, Word, Excel, Outlook, OneNote, meeting, presentation, mute, camera, next slide, previous slide, and screen sharing.
Never invent a request. If genuinely unintelligible, output exactly __UNCLEAR__.
Output only normalized plain text without labels, JSON, Markdown, or quotation marks.
`.trim()
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

  private waitForDataChannel(channel: RTCDataChannel): Promise<void> {
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
      }
      const onOpen = () => {
        cleanup()
        resolve()
      }
      const onError = () => {
        cleanup()
        reject(new Error('Could not open GPT Realtime data channel.'))
      }
      channel.addEventListener('open', onOpen)
      channel.addEventListener('error', onError)
    })
  }

  private rejectPending(error: Error): void {
    this.pendingCommit?.reject(error)
    const pending = this.pendingResponse
    if (pending) {
      this.clearPendingTimers(pending)
      this.pendingResponse = null
      pending.reject(error)
    }
  }

  private async shutdownConnection(): Promise<void> {
    this.rejectPending(new Error('GPT Realtime connection reset.'))
    await this.restoreSilentTrack().catch(() => undefined)
    this.dc?.close()
    this.pc?.close()
    this.remoteAudio?.remove()
    this.dc = null
    this.pc = null
    this.sender = null
    this.remoteAudio = null
    this.captureStartedAt = 0
  }
}

export const realtimeAgent = new PersistentRealtimeAgent()
