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
      'Convert one clear Smart Office request into one to eight ordered, bounded actions. This is the only model-facing execution function for PowerPoint, volume, brightness, local presentation summaries, approval-gated Classic Outlook drafts, and separately approved sending to Backend-allowlisted recipients.',
    parameters: {
      type: 'object',
      properties: {
        steps: {
          type: 'array',
          minItems: 1,
          maxItems: 8,
          description:
            'Exact ordered actions requested by the user. Recipient aliases are resolved deterministically by application code and injected after semantic planning. Draft creation and sending are separate approval steps. Raw email addresses and unrestricted sending are never accepted.',
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
                description: 'Output language for a generated summary or Outlook draft.',
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
                  'Use latest_verified only with outlook_send_approved_draft. The Backend resolves the newest verified unsent draft.',
              },
              recipient_key: {
                type: 'string',
                pattern: '^[a-z0-9][a-z0-9_-]{0,31}$',
                description:
                  'Optional Backend allowlist alias. Application code overwrites this field with the deterministically resolved recipient key when the user names a configured recipient.',
              },
              subject: {
                type: 'string',
                maxLength: 180,
                description:
                  'Optional Outlook draft subject. Sender account and recipient email are resolved by the Backend and cannot be supplied by the model.',
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
  resolvedRecipientKey: string | null
  timer: number
  resolve: (decision: RealtimeOfficeDecision) => void
  reject: (error: Error) => void
}

type RecipientEntry = {
  key?: unknown
  name?: unknown
  email?: unknown
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

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function recipientCatalog(runtimeContext: unknown): RecipientEntry[] {
  const envelope = objectValue(runtimeContext)
  const status = objectValue(envelope?.status)
  const catalog = status?.recipient_catalog
  return Array.isArray(catalog) ? (catalog as RecipientEntry[]) : []
}

function matchesRecipientToken(text: string, token: string): boolean {
  const cleanToken = token.trim()
  if (!cleanToken) return false
  if (/[^\x00-\x7F]/.test(cleanToken) || cleanToken.includes('@')) {
    return text.toLocaleLowerCase().includes(cleanToken.toLocaleLowerCase())
  }
  return new RegExp(`(^|[^a-z0-9])${escapeRegExp(cleanToken.toLocaleLowerCase())}([^a-z0-9]|$)`, 'i').test(
    text,
  )
}

function resolveRecipientKey(text: string, runtimeContext: unknown): string | null {
  const matches = recipientCatalog(runtimeContext)
    .map((entry) => {
      const key = typeof entry.key === 'string' ? entry.key.trim().toLocaleLowerCase() : ''
      const name = typeof entry.name === 'string' ? entry.name.trim() : ''
      const email = typeof entry.email === 'string' ? entry.email.trim() : ''
      if (!key) return null
      const tokens = [email, name, key].filter(Boolean)
      const score = Math.max(
        0,
        ...tokens.map((token) => (matchesRecipientToken(text, token) ? token.length : 0)),
      )
      return score > 0 ? { key, score } : null
    })
    .filter((value): value is { key: string; score: number } => value !== null)
    .sort((left, right) => right.score - left.score)

  if (!matches.length) return null
  if (matches.length > 1 && matches[0].score === matches[1].score) return null
  return matches[0].key
}

function applyResolvedRecipient(
  toolCall: RealtimeOfficeToolCall,
  recipientKey: string | null,
): RealtimeOfficeToolCall {
  if (!recipientKey) return toolCall
  const steps = toolCall.arguments.steps
  if (!Array.isArray(steps)) return toolCall
  const normalizedSteps = steps.map((step) => {
    const item = objectValue(step)
    if (!item) return step
    if (
      item.name === 'outlook_create_summary_draft' ||
      item.name === 'outlook_send_approved_draft'
    ) {
      return { ...item, recipient_key: recipientKey }
    }
    return item
  })
  return {
    ...toolCall,
    arguments: { ...toolCall.arguments, steps: normalizedSteps },
  }
}

function normaliseOfficeCommand(text: string): string {
  return text
    .toLocaleLowerCase()
    .replace(/\bp\s*[.\-_]?\s*p\s*[.\-_]?\s*t\b/gi, 'ppt')
    .replace(/\bpower\s+point\b/gi, 'powerpoint')
    .replace(/幻\s*灯\s*片/g, '幻灯片')
    .replace(/\s+/g, ' ')
    .trim()
}

function boundedPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)))
}

function numericValue(text: string): number | null {
  const match = text.match(/(?:百分之\s*)?(\d{1,3})(?:\s*%|\s*percent)?/i)
  if (!match) return null
  return boundedPercent(Number(match[1]))
}

function officePlan(steps: Array<Record<string, unknown>>): RealtimeOfficeDecision {
  const toolCall: RealtimeOfficeToolCall = {
    name: 'office_plan',
    arguments: { steps },
    call_id: null,
    source: 'gpt_realtime',
  }
  console.info('[OfficePlan]', {
    source: 'deterministic_common_command',
    steps,
  })
  return { kind: 'tool_call', toolCall }
}

function deterministicCommonOfficeDecision(text: string): RealtimeOfficeDecision | null {
  const clean = normaliseOfficeCommand(text)
  const number = numericValue(clean)

  const volumeMentioned = /音量|系统声音|电脑声音|扬声器声音|\bvolume\b|\baudio volume\b/i.test(clean)
  if (volumeMentioned || /静音|取消静音|\bmute\b|\bunmute\b/i.test(clean)) {
    if (/取消静音|\bunmute\b/i.test(clean) && number === null) {
      return {
        kind: 'clarify',
        clarification: /[\u3400-\u9fff]/.test(clean)
          ? '请告诉我取消静音后希望设置到多少音量，例如 40%。'
          : 'What volume should I restore, for example 40 percent?',
      }
    }
    if (/静音|\bmute\b/i.test(clean) && !/取消静音|\bunmute\b/i.test(clean)) {
      return officePlan([{ name: 'system_set_volume', value_percent: 0 }])
    }
    const absolute =
      number !== null &&
      (/(?:调|设|改|设置|调整).{0,6}(?:到|为|至)|百分之|%|\bto\b|\bat\b/i.test(clean) ||
        !/(调大|提高|增大|升高|调小|降低|减小|turn up|turn down|increase|decrease|raise|lower)/i.test(clean))
    if (absolute && number !== null) {
      return officePlan([{ name: 'system_set_volume', value_percent: number }])
    }
    if (/(调大|提高|增大|升高|大一点|turn up|increase|raise|louder|higher)/i.test(clean)) {
      return officePlan([{ name: 'system_adjust_volume', delta_percent: number ?? 10 }])
    }
    if (/(调小|降低|减小|小一点|turn down|decrease|lower|quieter)/i.test(clean)) {
      return officePlan([{ name: 'system_adjust_volume', delta_percent: -(number ?? 10) }])
    }
    if (/多少|几|当前|what|current/i.test(clean)) {
      return officePlan([{ name: 'system_get_status' }])
    }
  }

  const pptMentioned = /ppt|powerpoint|幻灯片|演示文稿|presentation|slides?/i.test(clean)
  const startShow = /开始演示|开始放映|启动演示|启动放映|进入演示|进入放映|播放幻灯片|演示ppt|放映ppt|播放ppt|start (?:the )?(?:slide ?show|slideshow|presentation)|present (?:the )?(?:ppt|powerpoint|presentation)/i.test(clean)
  const openPpt = /打开|开启|open|launch/i.test(clean) && pptMentioned
  if (openPpt && startShow) {
    return officePlan([
      { name: 'presentation_open_configured' },
      { name: 'presentation_start_slideshow' },
    ])
  }
  if (startShow) return officePlan([{ name: 'presentation_start_slideshow' }])
  if (openPpt) return officePlan([{ name: 'presentation_open_configured' }])
  if (/下一页|下一张|后一页|后一张|向后翻|往后翻|next slide/i.test(clean)) {
    return officePlan([{ name: 'presentation_next_slide' }])
  }
  if (/上一页|上一张|前一页|前一张|向前翻|往前翻|previous slide/i.test(clean)) {
    return officePlan([{ name: 'presentation_previous_slide' }])
  }
  if (/最后一页|末页|last slide|final slide/i.test(clean)) {
    return officePlan([{ name: 'presentation_go_to_slide', slide_target: 'last' }])
  }
  const slideNumber = clean.match(/(?:第\s*(\d+)\s*页|(?:go|jump|move)\s+to\s+slide\s+(\d+))/i)
  if (slideNumber) {
    return officePlan([{
      name: 'presentation_go_to_slide',
      slide_number: Number(slideNumber[1] ?? slideNumber[2]),
    }])
  }
  if (/结束演示|结束放映|停止演示|停止放映|退出演示|退出放映|end (?:the )?(?:show|slideshow)|exit (?:the )?slideshow/i.test(clean)) {
    return officePlan([{ name: 'presentation_end_slideshow' }])
  }
  if (/演示状态|放映状态|ppt状态|powerpoint status|presentation status/i.test(clean)) {
    return officePlan([{ name: 'presentation_get_status' }])
  }
  return null
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

    const deterministic = deterministicCommonOfficeDecision(clean)
    if (deterministic) {
      this.recentUtterances = [...this.recentUtterances, clean].slice(-MAX_HISTORY_ITEMS)
      return deterministic
    }

    await this.ensureConnected()
    if (this.pending) throw new Error('A GPT Realtime office decision is still active.')

    const runtimeContext = await fetchJsonWithTimeout(`${API_BASE_URL}/api/office/status`)
    const resolvedRecipientKey = resolveRecipientKey(clean, runtimeContext)
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
        resolvedRecipientKey,
        timer,
        resolve,
        reject,
      }

      const languageLabel = language === 'zh' ? 'Chinese Mandarin' : 'English'
      const historyText = historyBeforeCurrent.length
        ? historyBeforeCurrent.map((item, index) => `${index + 1}. ${item}`).join('\n')
        : '(none)'
      const contextText = JSON.stringify(runtimeContext).slice(0, 8000)
      const recipientResolution = resolvedRecipientKey
        ? `Application code has deterministically resolved the named recipient to recipient_key="${resolvedRecipientKey}". Do not inspect, confirm, reject, or clarify this mapping. Plan only the requested semantic actions; application code will inject this key into Outlook steps.`
        : 'Application code did not resolve a configured recipient from the utterance. For draft creation without a named recipient, omit recipient_key so the Backend applies its configured default. Clarify only when the user explicitly names an unresolved person or email.'

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

Deterministic recipient resolution performed by application code:
${recipientResolution}

Recent office utterances in this browser session:
${historyText}

Observed Backend runtime context:
${contextText}

Rules:
- For every clear supported office request, call office_plan exactly once.
- Put exactly one step in the plan for one requested action. Put two to eight ordered steps for a compound request.
- Preserve the exact user-requested order. Do not silently add PowerPoint open/start prerequisites.
- “演示PPT/开始演示/开始放映/播放幻灯片/start the slideshow/start presenting” means presentation_start_slideshow.
- “打开并演示PPT/open and present the PowerPoint” means presentation_open_configured followed by presentation_start_slideshow.
- Supported actions are only the enum values in the schema. Never invent a file path, sender, recipient, raw email address, application, shell command, COM method, approval, EntryID, or success result.
- GPT Realtime performs semantic action planning only. Recipient-file lookup, name matching, allowlist validation, and final recipient_key selection belong to application code and the Backend.
- When application code supplies a resolved recipient key above, never question it and never return CLARIFY because of that recipient. You may omit recipient_key; application code injects the resolved key after planning.
- PowerPoint direction is deterministic: presentation_next_slide increases the page number toward the end; presentation_previous_slide decreases it toward the beginning.
- Chinese convention for this application: “向前翻/往前翻/翻回前面/上一页/前一页” means previous. “向后翻/往后翻/下一页/后一页/继续往下” means next. “前进两页” means next twice.
- Repeat next or previous steps when multiple slides are requested. “向前翻两页” is previous twice; “向后翻两页” is next twice.
- “最后一页/末页/last slide/final slide” uses presentation_go_to_slide with slide_target="last". Never ask for its numeric page.
- For explicit absolute volume or brightness, use system_set_volume/system_set_brightness with value_percent.
- For relative volume or brightness, use system_adjust_volume/system_adjust_brightness with signed delta_percent. When the user says only “一点/a little” without a number, use 10 percentage points in the requested direction.
- Questions asking for current volume or brightness use system_get_status.
- “生成演示摘要/summarize the presentation” uses office_generate_presentation_summary with the user's language. It writes only to the configured local LOG directory.
- When the user does not name a recipient for draft creation, omit recipient_key so the Backend applies the configured default recipient.
- When the user explicitly names a person or email that application code did not resolve, do not invent a key and do not pass the raw email. Return CLARIFY: followed by a concise request to configure or choose an available recipient.
- When the user asks to prepare an Outlook draft from the current presentation and does not explicitly request the existing/latest summary, include office_generate_presentation_summary first, followed by outlook_create_summary_draft with summary_source="latest".
- outlook_create_summary_draft uses the fixed signed-in Classic Outlook sender account and the Backend-resolved allowlisted recipient. The first Backend approval is mandatory before creating and displaying the draft.
- When the user explicitly asks to send an already-created/verified draft, use exactly one outlook_send_approved_draft step with draft_source="latest_verified". Application code injects a resolved recipient key when one was named.
- When the user explicitly asks to prepare and then send in one request, create separate outlook_create_summary_draft and outlook_send_approved_draft steps. The Backend will pause separately before the draft step and again before the send step.
- outlook_send_approved_draft can only send a verified unsent draft whose recipient is still in the Backend allowlist. Before Send(), the Backend removes the sentence saying the message is only a draft and not yet sent, saves and re-verifies the sender and sole recipient, then invokes Outlook Send().
- There is no send path without a second Backend approval. Never treat draft approval as send approval, and never send arbitrary recipients or arbitrary Outlook items.
- Explicit approval/cancel/skip/takeover utterances for an already-running task are handled by the Backend router. Return exactly NO_OFFICE_ACTION for those utterances.
- Do not call a function for reception questions, ordinary conversation, Teams, Zoom, Word, Excel, unsupported device controls, or document generation beyond the bounded presentation summary. Return exactly NO_OFFICE_ACTION.
- Ask for clarification only when the intended action or value genuinely remains ambiguous, or when the user explicitly named an unresolved recipient.
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
          'You are a bounded Smart Office semantic planner. Application code and the Backend resolve and validate recipients deterministically. Never use raw email addresses, never bypass Backend approvals, and never claim success.',
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
        pending.toolCall = applyResolvedRecipient(
          {
            name: 'office_plan',
            arguments: safeArguments(event.arguments),
            call_id: event.call_id ?? null,
            source: 'gpt_realtime',
          },
          pending.resolvedRecipientKey,
        )
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
        console.info('[OfficePlan]', {
          source: 'gpt_realtime',
          steps: pending.toolCall.arguments.steps ?? [],
          requestId: pending.requestId,
        })
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
