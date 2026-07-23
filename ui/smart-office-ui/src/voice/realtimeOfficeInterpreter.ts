const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

const CONNECTION_TIMEOUT_MS = 15_000
const DECISION_TIMEOUT_MS = 20_000
const CONTEXT_TIMEOUT_MS = 3_000
const MAX_HISTORY_ITEMS = 6

export type OfficeToolName = 'office_plan'

export type RealtimeOfficeToolCall = {
  name: OfficeToolName
  arguments: Record<string, unknown>
  call_id: string | null
  source: 'gpt_realtime'
}

export type RealtimeOfficeDecision =
  | { kind: 'tool_call'; toolCall: RealtimeOfficeToolCall }
  | { kind: 'clarify'; clarification: string }
  | { kind: 'none'; reason: string }

const OFFICE_ACTIONS = [
  'presentation_open_configured',
  'presentation_start_slideshow',
  'presentation_next_slide',
  'presentation_previous_slide',
  'presentation_go_to_slide',
  'presentation_get_status',
  'presentation_end_slideshow',
  'system_get_status',
  'system_set_volume',
  'system_adjust_volume',
  'system_set_brightness',
  'system_adjust_brightness',
  'office_generate_presentation_summary',
  'outlook_create_summary_draft',
  'outlook_send_approved_draft',
] as const

const OFFICE_TOOLS = [
  {
    type: 'function',
    name: 'office_plan',
    description:
      'Convert one clear Smart Office request into one to eight ordered, bounded actions. This is the only model-facing execution function for PowerPoint, volume, brightness, local presentation summaries, approval-gated Classic Outlook draft creation, and separately approved sending of the latest verified draft.',
    parameters: {
      type: 'object',
      properties: {
        steps: {
          type: 'array',
          minItems: 1,
          maxItems: 8,
          description:
            'Exact ordered actions requested by the user. PowerPoint actions remain bounded to the configured presentation. Outlook draft creation and Outlook sending are separate Backend approval steps. Unrestricted or arbitrary sending is never allowed.',
          items: {
            type: 'object',
            properties: {
              name: {
                type: 'string',
                enum: OFFICE_ACTIONS,
              },
              slide_number: {
                type: 'integer',
                minimum: 1,
                description:
                  'Concrete one-based page number. Use only with presentation_go_to_slide and never combine with slide_target.',
              },
              slide_target: {
                type: 'string',
                enum: ['last'],
                description:
                  'Use slide_target="last" for the semantic final slide. The Backend resolves the live total slide count.',
              },
              value_percent: {
                type: 'integer',
                minimum: 0,
                maximum: 100,
                description:
                  'Absolute volume or brightness percentage. Use only with system_set_volume or system_set_brightness.',
              },
              delta_percent: {
                type: 'integer',
                minimum: -100,
                maximum: 100,
                description:
                  'Signed percentage-point adjustment. Use only with system_adjust_volume or system_adjust_brightness. Never use zero.',
              },
              language: {
                type: 'string',
                enum: ['zh', 'en'],
                description:
                  'Output language for a generated summary or Outlook draft.',
              },
              summary_source: {
                type: 'string',
                enum: ['latest'],
                description:
                  'Use latest for outlook_create_summary_draft. The Backend resolves the newest verified local summary artifact.',
              },
              draft_source: {
                type: 'string',
                enum: ['latest_verified'],
                description:
                  'Use latest_verified only with outlook_send_approved_draft. The Backend resolves the newest verified unsent draft and never accepts an EntryID, sender, or recipient from the model.',
              },
              subject: {
                type: 'string',
                maxLength: 180,
                description:
                  'Optional Outlook draft subject. Sender account and recipient are fixed by Backend configuration and cannot be supplied by the model.',
              },
            },
            required: ['name'],
            additionalProperties: false,
          },
        },
      },
      required: ['steps'],
      additionalProperties: false,
    },
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
  toolCall: RealtimeOfficeToolCall | null
  timer: number
  resolve: (decision: RealtimeOfficeDecision) => void
  reject: (error: Error) => void
}

type AudioContextConstructor = new () => AudioContext

declare global {
  interface Window {
    webkitAudioContext?: AudioContextConstructor
  }
}

function interpreterConversationId(): string {
  const key = 'smartoffice_realtime_office_interpreter_id'
  const existing = sessionStorage.getItem(key)
  if (existing) return existing
  const value = `office-interpreter-${crypto.randomUUID()}`
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

async function fetchJsonWithTimeout(url: string): Promise<unknown> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), CONTEXT_TIMEOUT_MS)
  try {
    const response = await fetch(url, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
    if (!response.ok) return { unavailable: true, status: response.status }
    return await response.json()
  } catch (error) {
    return {
      unavailable: true,
      error: error instanceof Error ? error.message : String(error),
    }
  } finally {
    window.clearTimeout(timer)
  }
}

class RealtimeOfficeInterpreter {
  private pc: RTCPeerConnection | null = null
  private dc: RTCDataChannel | null = null
  private connectPromise: Promise<void> | null = null
  private pending: PendingDecision | null = null
  private silentContext: AudioContext | null = null
  private silentTrack: MediaStreamTrack | null = null
  private silentStream: MediaStream | null = null
  private recentUtterances: string[] = []

  async interpret(text: string, language: 'zh' | 'en'): Promise<RealtimeOfficeDecision> {
    const clean = text.trim()
    if (!clean || clean === '__UNCLEAR__') {
      return { kind: 'none', reason: 'empty_or_unclear' }
    }
    await this.ensureConnected()
    if (this.pending) throw new Error('A GPT Realtime office decision is still active.')

    const runtimeContext = await fetchJsonWithTimeout(`${API_BASE_URL}/api/office/status`)
    const historyBeforeCurrent = [...this.recentUtterances]
    this.recentUtterances = [...this.recentUtterances, clean].slice(-MAX_HISTORY_ITEMS)

    return await new Promise<RealtimeOfficeDecision>((resolve, reject) => {
      const requestId = `office-intent-${Date.now()}-${Math.random().toString(16).slice(2)}`
      const timer = window.setTimeout(() => {
        if (this.pending?.requestId !== requestId) return
        this.pending = null
        this.safeSend({ type: 'response.cancel' })
        reject(new Error('GPT Realtime office intent decision timed out.'))
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
      const historyText = historyBeforeCurrent.length
        ? historyBeforeCurrent.map((item, index) => `${index + 1}. ${item}`).join('\n')
        : '(none)'
      const contextText = JSON.stringify(runtimeContext).slice(0, 6000)

      this.send({
        type: 'response.create',
        response: {
          conversation: 'none',
          output_modalities: ['text'],
          metadata: {
            purpose: 'office_intent',
            request_id: requestId,
          },
          tool_choice: 'auto',
          tools: OFFICE_TOOLS,
          instructions: `
Interpret one user utterance for Smart Office Phase 3.
Language: ${languageLabel}.
Current user utterance: ${clean}

Recent office utterances in this browser session:
${historyText}

Observed Backend runtime context:
${contextText}

Rules:
- For every clear supported office request, call office_plan exactly once.
- Put exactly one step in the plan for one requested action. Put two to eight ordered steps for a compound request.
- Preserve the exact user-requested order. Do not silently add PowerPoint open/start prerequisites.
- Supported actions are only the enum values in the schema. Never invent a file path, sender, recipient, application, shell command, COM method, approval, EntryID, or success result.
- PowerPoint direction is deterministic: presentation_next_slide increases the page number toward the end; presentation_previous_slide decreases it toward the beginning.
- Chinese convention for this application: “向前翻/往前翻/翻回前面/上一页/前一页” means previous. “向后翻/往后翻/下一页/后一页/继续往下” means next. “前进两页” means next twice.
- Repeat next or previous steps when multiple slides are requested. “向前翻两页” is previous twice; “向后翻两页” is next twice.
- “最后一页/末页/last slide/final slide” uses presentation_go_to_slide with slide_target="last". Never ask for its numeric page.
- For explicit absolute volume or brightness, use system_set_volume/system_set_brightness with value_percent.
- For relative volume or brightness, use system_adjust_volume/system_adjust_brightness with signed delta_percent. When the user says only “一点/a little” without a number, use 10 percentage points in the requested direction.
- Questions asking for current volume or brightness use system_get_status.
- “生成演示摘要/summarize the presentation” uses office_generate_presentation_summary with the user's language. It writes only to the configured local LOG directory.
- When the user asks to prepare an Outlook draft, email draft, or mail draft from the current presentation and does not explicitly request the existing/latest summary, include office_generate_presentation_summary first, followed by outlook_create_summary_draft with summary_source="latest".
- outlook_create_summary_draft uses the fixed signed-in Classic Outlook sender account and fixed Backend recipient. The first Backend approval is mandatory before creating and displaying the draft.
- When the user explicitly asks to send an already-created/verified draft, use exactly one outlook_send_approved_draft step with draft_source="latest_verified".
- When the user explicitly asks to prepare and then send in one request, plan office_generate_presentation_summary when needed, then outlook_create_summary_draft, then outlook_send_approved_draft. The Backend will pause separately before the draft step and again before the send step.
- outlook_send_approved_draft can only send the latest verified unsent draft with the fixed Backend sender and recipient. Before Send(), the Backend removes the sentence saying the message is only a draft and not yet sent, saves and re-verifies the edit, then invokes Outlook Send().
- There is no send tool without a second Backend approval. Never treat draft approval as send approval, and never send arbitrary recipients or arbitrary Outlook items.
- Explicit approval/cancel/skip/takeover utterances for an already-running task are handled by the Backend router. Return exactly NO_OFFICE_ACTION for those utterances.
- Do not call a function for reception questions, ordinary conversation, Teams, Zoom, Word, Excel, unsupported device controls, or document generation beyond the bounded presentation summary. Return exactly NO_OFFICE_ACTION.
- Ask for clarification only when the intended action or required value genuinely remains ambiguous after applying these rules and the supplied context.
- Do not answer the user and do not claim an action succeeded.
`.trim(),
        },
      })
    })
  }

  async shutdown(): Promise<void> {
    this.rejectPending(new Error('GPT Realtime office interpreter shut down.'))
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
      throw new Error('Could not create a silent WebRTC track for office intent interpretation.')
    }

    const pc = new RTCPeerConnection()
    const dc = pc.createDataChannel('oai-events-office-intent')
    pc.addTrack(this.silentTrack, this.silentStream)
    this.pc = pc
    this.dc = dc
    dc.addEventListener('message', (event: MessageEvent<string>) => this.handleEvent(event))
    pc.addEventListener('connectionstatechange', () => {
      if (['failed', 'closed'].includes(pc.connectionState)) {
        this.rejectPending(new Error('GPT Realtime office interpreter connection was lost.'))
      }
    })

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    const sdp = pc.localDescription?.sdp ?? offer.sdp
    if (!sdp) throw new Error('Could not create office interpreter WebRTC offer.')
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
      throw new Error(`Realtime office session failed: ${sessionResponse.status} ${detail}`)
    }
    await pc.setRemoteDescription({ type: 'answer', sdp: await sessionResponse.text() })
    await this.waitForDataChannel(dc)
    this.send({
      type: 'session.update',
      session: {
        type: 'realtime',
        output_modalities: ['text'],
        tool_choice: 'auto',
        tools: OFFICE_TOOLS,
        instructions:
          'You are a bounded Smart Office planner. For clear supported requests, call the single office_plan function. Never execute tools yourself, never bypass Backend approvals, and never claim success.',
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
      if (event.name === 'office_plan') {
        pending.toolCall = {
          name: 'office_plan',
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
          'GPT Realtime office decision failed.'
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
      pending.resolve({ kind: 'none', reason: text || 'NO_OFFICE_ACTION' })
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
      throw new Error('GPT Realtime office interpreter data channel is not open.')
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
        reject(new Error('Timed out while opening office interpreter data channel.'))
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
        reject(new Error('Office interpreter data channel failed to open.'))
      }
      channel.addEventListener('open', onOpen)
      channel.addEventListener('error', onError)
    })
  }
}

export const realtimeOfficeInterpreter = new RealtimeOfficeInterpreter()
