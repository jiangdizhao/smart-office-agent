const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

const CONNECTION_TIMEOUT_MS = 15_000
const DECISION_TIMEOUT_MS = 20_000

export type PresentationToolName =
  | 'presentation_open_configured'
  | 'presentation_start_slideshow'
  | 'presentation_next_slide'
  | 'presentation_previous_slide'
  | 'presentation_go_to_slide'
  | 'presentation_get_status'
  | 'presentation_end_slideshow'

export type RealtimePresentationToolCall = {
  name: PresentationToolName
  arguments: Record<string, unknown>
  call_id: string | null
  source: 'gpt_realtime'
}

export type RealtimePresentationDecision =
  | { kind: 'tool_call'; toolCall: RealtimePresentationToolCall }
  | { kind: 'clarify'; clarification: string }
  | { kind: 'none'; reason: string }

const PRESENTATION_TOOLS = [
  {
    type: 'function',
    name: 'presentation_open_configured',
    description:
      'Open the configured demo presentation Loss.pptx. Use only when the user clearly asks to open the presentation or PowerPoint demo.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    type: 'function',
    name: 'presentation_start_slideshow',
    description:
      'Start the slide show for the configured presentation. Use when the user clearly asks to start, play, present, or begin the slide show.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    type: 'function',
    name: 'presentation_next_slide',
    description:
      'Advance exactly one slide. Chinese examples include 下一页, 下一张, 翻到下一页, 继续往后. English examples include next slide, move forward, advance one slide.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    type: 'function',
    name: 'presentation_previous_slide',
    description:
      'Return exactly one slide. Chinese examples include 上一页, 上一张, 回到前一页. English examples include previous slide, go back one slide.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    type: 'function',
    name: 'presentation_go_to_slide',
    description:
      'Jump to one explicitly requested slide number. Never invent a slide number.',
    parameters: {
      type: 'object',
      properties: {
        slide_number: {
          type: 'integer',
          minimum: 1,
          description: 'The explicitly requested one-based slide number.',
        },
      },
      required: ['slide_number'],
      additionalProperties: false,
    },
  },
  {
    type: 'function',
    name: 'presentation_get_status',
    description:
      'Inspect presentation status, including whether it is open, whether the slide show is active, and the current slide. Use for questions such as 现在是第几页 or what slide are we on.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    type: 'function',
    name: 'presentation_end_slideshow',
    description:
      'End the active slide show without closing the PowerPoint presentation. Use when the user clearly asks to end or stop the presentation.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  },
] as const

type ServerEvent = {
  type?: string
  delta?: string
  text?: string
  name?: string
  arguments?: string
  call_id?: string
  error?: { code?: string; message?: string }
  response?: {
    status?: string
    metadata?: Record<string, unknown>
    status_details?: { error?: { message?: string } }
  }
}

type PendingDecision = {
  requestId: string
  text: string
  toolCall: RealtimePresentationToolCall | null
  timer: number
  resolve: (decision: RealtimePresentationDecision) => void
  reject: (error: Error) => void
}

type AudioContextConstructor = new () => AudioContext

declare global {
  interface Window {
    webkitAudioContext?: AudioContextConstructor
  }
}

function interpreterConversationId(): string {
  const key = 'smartoffice_realtime_presentation_interpreter_id'
  const existing = sessionStorage.getItem(key)
  if (existing) return existing
  const value = `presentation-interpreter-${crypto.randomUUID()}`
  sessionStorage.setItem(key, value)
  return value
}

function safeArguments(value: string | undefined): Record<string, unknown> {
  if (!value) return {}
  try {
    const parsed = JSON.parse(value) as unknown
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
  } catch {
    return {}
  }
  return {}
}

class RealtimePresentationInterpreter {
  private pc: RTCPeerConnection | null = null
  private dc: RTCDataChannel | null = null
  private connectPromise: Promise<void> | null = null
  private pending: PendingDecision | null = null
  private silentContext: AudioContext | null = null
  private silentTrack: MediaStreamTrack | null = null
  private silentStream: MediaStream | null = null

  async interpret(text: string, language: 'zh' | 'en'): Promise<RealtimePresentationDecision> {
    const clean = text.trim()
    if (!clean || clean === '__UNCLEAR__') {
      return { kind: 'none', reason: 'empty_or_unclear' }
    }
    await this.ensureConnected()
    if (this.pending) throw new Error('A GPT Realtime presentation decision is still active.')

    return await new Promise<RealtimePresentationDecision>((resolve, reject) => {
      const requestId = `presentation-intent-${Date.now()}-${Math.random().toString(16).slice(2)}`
      const timer = window.setTimeout(() => {
        if (this.pending?.requestId !== requestId) return
        this.pending = null
        this.safeSend({ type: 'response.cancel' })
        reject(new Error('GPT Realtime presentation intent decision timed out.'))
      }, DECISION_TIMEOUT_MS)
      this.pending = {
        requestId,
        text: '',
        toolCall: null,
        timer,
        resolve,
        reject,
      }

      const languageLabel = language === 'zh' ? 'Chinese Mandarin' : 'English'
      this.send({
        type: 'response.create',
        response: {
          conversation: 'none',
          output_modalities: ['text'],
          metadata: {
            purpose: 'presentation_intent',
            request_id: requestId,
          },
          tool_choice: 'auto',
          tools: PRESENTATION_TOOLS,
          instructions: `
Interpret one user utterance for Smart Office Gate 2A.
Language: ${languageLabel}.
User utterance: ${clean}

Rules:
- For one clear supported presentation action, call exactly one matching function.
- Understand natural Chinese and English paraphrases; do not require fixed wording.
- Do not call a function for reception questions, ordinary conversation, Teams, Word, Excel, email, volume, brightness, document generation, or any unsupported action. Return exactly NO_PRESENTATION_ACTION instead.
- Gate 2A supports only one action per turn. If the utterance contains two or more requested actions, return exactly COMPOUND_PRESENTATION_ACTION.
- If it sounds presentation-related but the intended action or required slide number is genuinely ambiguous, return CLARIFY: followed by one concise question in the user's language.
- Never invent a slide number, file path, action, approval, or success result.
- Do not answer the user and do not claim an action succeeded.
`.trim(),
        },
      })
    })
  }

  async shutdown(): Promise<void> {
    this.rejectPending(new Error('GPT Realtime presentation interpreter shut down.'))
    this.dc?.close()
    this.pc?.close()
    this.silentTrack?.stop()
    this.dc = null
    this.pc = null
    this.silentTrack = null
    this.silentStream = null
    await this.silentContext?.close().catch(() => undefined)
    this.silentContext = null
  }

  private async ensureConnected(): Promise<void> {
    if (
      this.pc &&
      this.dc?.readyState === 'open' &&
      ['connected', 'connecting', 'new'].includes(this.pc.connectionState)
    ) {
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
    await this.shutdown().catch(() => undefined)
    const statusResponse = await fetch(`${API_BASE_URL}/api/realtime/status`, {
      headers: { Accept: 'application/json' },
    })
    if (!statusResponse.ok) {
      throw new Error(`Realtime status failed: ${statusResponse.status}`)
    }
    const status = (await statusResponse.json()) as { configured?: boolean; enabled?: boolean }
    if (!status.configured || !status.enabled) {
      throw new Error('GPT Realtime is not configured in the Backend process.')
    }

    this.createSilentTrack()
    if (!this.silentTrack || !this.silentStream) {
      throw new Error('Could not create a silent WebRTC track for intent interpretation.')
    }

    const pc = new RTCPeerConnection()
    const dc = pc.createDataChannel('oai-events-presentation-intent')
    pc.addTrack(this.silentTrack, this.silentStream)
    this.pc = pc
    this.dc = dc
    dc.addEventListener('message', (event: MessageEvent<string>) => this.handleEvent(event))
    pc.addEventListener('connectionstatechange', () => {
      if (['failed', 'closed'].includes(pc.connectionState)) {
        this.rejectPending(new Error('GPT Realtime presentation interpreter connection was lost.'))
      }
    })

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    const sdp = pc.localDescription?.sdp ?? offer.sdp
    if (!sdp) throw new Error('Could not create presentation interpreter WebRTC offer.')
    const sessionResponse = await fetch(
      `${API_BASE_URL}/api/realtime/session?conversation_id=${encodeURIComponent(interpreterConversationId())}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: sdp,
      },
    )
    if (!sessionResponse.ok) {
      const detail = await sessionResponse.text().catch(() => '')
      throw new Error(`Realtime presentation session failed: ${sessionResponse.status} ${detail}`)
    }
    await pc.setRemoteDescription({ type: 'answer', sdp: await sessionResponse.text() })
    await this.waitForDataChannel(dc)
    this.send({
      type: 'session.update',
      session: {
        type: 'realtime',
        output_modalities: ['text'],
        tool_choice: 'auto',
        tools: PRESENTATION_TOOLS,
        instructions:
          'You are a bounded presentation-intent interpreter. Select only registered presentation functions. Never execute tools yourself and never claim success.',
        audio: { input: { turn_detection: null } },
      },
    })
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

  private handleEvent(message: MessageEvent<string>): void {
    let event: ServerEvent
    try {
      event = JSON.parse(message.data) as ServerEvent
    } catch {
      return
    }
    const pending = this.pending
    if (!pending) return

    if (event.type === 'response.function_call_arguments.done') {
      const name = event.name as PresentationToolName | undefined
      if (name) {
        pending.toolCall = {
          name,
          arguments: safeArguments(event.arguments),
          call_id: event.call_id ?? null,
          source: 'gpt_realtime',
        }
      }
      return
    }
    if (event.type === 'response.output_text.delta') {
      pending.text += event.delta ?? ''
      return
    }
    if (event.type === 'response.output_text.done') {
      pending.text = event.text ?? pending.text
      return
    }
    if (event.type === 'response.done') {
      const responseRequestId = event.response?.metadata?.request_id
      if (typeof responseRequestId === 'string' && responseRequestId !== pending.requestId) {
        return
      }
      if (event.response?.status === 'failed') {
        const detail =
          event.response.status_details?.error?.message ??
          'GPT Realtime presentation decision failed.'
        this.finishWithError(pending, new Error(detail))
        return
      }
      window.clearTimeout(pending.timer)
      this.pending = null
      if (pending.toolCall) {
        pending.resolve({ kind: 'tool_call', toolCall: pending.toolCall })
        return
      }
      const text = pending.text.trim()
      if (text.toUpperCase().startsWith('CLARIFY:')) {
        pending.resolve({
          kind: 'clarify',
          clarification: text.slice(text.indexOf(':') + 1).trim(),
        })
        return
      }
      pending.resolve({ kind: 'none', reason: text || 'NO_PRESENTATION_ACTION' })
      return
    }
    if (event.type === 'error') {
      const code = event.error?.code ?? ''
      if (code === 'response_cancel_not_active') return
      this.finishWithError(
        pending,
        new Error(event.error?.message ?? 'GPT Realtime returned an unknown error.'),
      )
    }
  }

  private finishWithError(pending: PendingDecision, error: Error): void {
    window.clearTimeout(pending.timer)
    if (this.pending?.requestId === pending.requestId) this.pending = null
    pending.reject(error)
  }

  private rejectPending(error: Error): void {
    const pending = this.pending
    if (!pending) return
    this.finishWithError(pending, error)
  }

  private send(event: Record<string, unknown>): void {
    if (this.dc?.readyState !== 'open') {
      throw new Error('GPT Realtime presentation interpreter data channel is not open.')
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
        reject(new Error('Timed out while opening presentation interpreter data channel.'))
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
        reject(new Error('Could not open presentation interpreter data channel.'))
      }
      channel.addEventListener('open', onOpen)
      channel.addEventListener('error', onError)
    })
  }
}

export const realtimePresentationInterpreter = new RealtimePresentationInterpreter()
