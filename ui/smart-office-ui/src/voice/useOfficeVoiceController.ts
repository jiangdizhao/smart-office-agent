import { useEffect, useRef, useState } from 'react'
import { ConversationAudioRecorder } from '../recording/conversationAudioRecorder'
import { BrowserSpeechCapture } from './browserSpeechRecognition'
import { realtimeAgent, type RealtimeRuntimeStatus, type VoiceLanguage } from './realtimeAgentRuntime'
import { realtimeOfficeInterpreter, type RealtimeOfficeToolCall } from './realtimeOfficeInterpreter'
import { voiceOutputManager, type VoiceOutputProvider } from './voiceOutputManager'

export const OFFICE_API_BASE =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

const TASK_TIMEOUT = 180_000
const CONVERSATION_POLL_MS = 3_000
const CJK = /[\u3400-\u9fff]/

export type OfficeActor = 'visitor' | 'employee' | 'operator'
export type OfficeAsrProvider = 'realtime' | 'browser'
export type OfficePanelState =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'error'
export type OfficeTaskStatus =
  | 'created'
  | 'planning'
  | 'running'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'
export type ApprovalAction = 'approve' | 'skip' | 'cancel'
export type ConversationPhase =
  | 'standby'
  | 'engaged'
  | 'awaiting_user'
  | 'task_active'
  | 'closing'

export type RecipientEntry = { key: string; name: string; email: string }

export type ConversationLedgerEntry = {
  entryId: string
  role: 'user' | 'assistant'
  text: string
  timestamp: number
  summarisable: boolean
}

export type ProximityDetection = {
  face_area_ratio: number
  confidence: number
  frontal_score: number
  center_x: number
  center_y: number
  stable_frames: number
  detector: string
}

export type OfficeStatus = {
  presentation_open?: boolean
  slideshow_active?: boolean
  current_slide?: number | null
  total_slides?: number | null
  target_monitor_device?: string | null
  slideshow_monitor_device?: string | null
  monitor_placement_enforced?: boolean
  volume_percent?: number | null
  brightness_percent?: number | null
  summary_path_relative?: string | null
  artifact_url?: string | null
  outlook_draft_created?: boolean
  outlook_draft_verified?: boolean
  outlook_draft_entry_id?: string | null
  outlook_draft_displayed?: boolean
  source_outlook_draft_entry_id?: string | null
  sender_account_email?: string | null
  default_recipient_key?: string | null
  recipient_key?: string | null
  recipient_name?: string | null
  recipient_email?: string | null
  recipient_catalog?: RecipientEntry[]
  allowed_recipient_keys?: string[]
  approval_gated_email_send_enabled?: boolean
  unrestricted_email_send_enabled?: boolean
  draft_notice_removed?: boolean
  send_invoked?: boolean
  sent?: boolean
  delivery_confirmed?: boolean
}

type ToolData = {
  office_status?: OfficeStatus
  presentation_status?: OfficeStatus
  verification?: { ok?: boolean; message?: string }
  artifact_url?: string
  summary_path_relative?: string
  outlook_draft_created?: boolean
  outlook_draft_verified?: boolean
  outlook_draft_entry_id?: string
  outlook_draft_displayed?: boolean
  source_outlook_draft_entry_id?: string
  sender_account_email?: string
  recipient_key?: string
  recipient_name?: string
  recipient_email?: string
  approval_gated_email_send_enabled?: boolean
  unrestricted_email_send_enabled?: boolean
  draft_notice_removed?: boolean
  send_invoked?: boolean
  sent?: boolean
  delivery_confirmed?: boolean
}

type TaskStep = {
  index: number
  tool_name?: string | null
  args?: { recipient_key?: string }
  status: string
  result?: { tool_name: string; ok: boolean; message: string; data?: ToolData } | null
}

type Task = {
  task_id: string
  status: OfficeTaskStatus
  steps: TaskStep[]
  summary?: string | null
}

type Turn = {
  route: string
  spoken_text: string
  scene: string
  permission_decision: string
  task_id: string | null
  task_status: string | null
  approval_required: boolean
  content_url: string | null
  source_ids?: string[]
  realtime_tool_call?: RealtimeOfficeToolCall | null
  tool_result?: { tool_name: string; ok: boolean; message: string; data?: ToolData } | null
  verification_result?: { ok: boolean; message: string } | null
  office_status?: OfficeStatus | null
  presentation_status?: OfficeStatus | null
}

type ConversationEnvelope = {
  ok?: boolean
  state?: {
    conversation_phase?: ConversationPhase
    active_task_id?: string | null
    last_visible_answer?: string
  }
}

type ProximityGreetingEnvelope = {
  ok?: boolean
  triggered?: boolean
  greeting?: string
  reason?: string
  conversation_phase?: ConversationPhase
}

export type OfficeVoiceController = {
  conversationId: string
  conversationPhase: ConversationPhase
  language: VoiceLanguage
  actor: OfficeActor
  asr: OfficeAsrProvider
  voice: VoiceOutputProvider
  panel: OfficePanelState
  runtime: RealtimeRuntimeStatus
  input: string
  transcript: string
  answer: string
  route: string
  permission: string
  tool: string
  verified: boolean | null
  taskId: string | null
  taskStatus: OfficeTaskStatus | null
  pendingApprovalTool: string | null
  pendingRecipientKey: string | null
  office: OfficeStatus | null
  contentUrl: string | null
  error: string
  listening: boolean
  busy: boolean
  active: boolean
  browserAsrAvailable: boolean
  recordingActive: boolean
  recordingAvailable: boolean
  recordingDurationSeconds: number
  recordingTurnCount: number
  recordingError: string
  setLanguage: (language: VoiceLanguage) => void
  setActor: (actor: OfficeActor) => void
  setAsr: (provider: OfficeAsrProvider) => void
  setVoice: (provider: VoiceOutputProvider) => Promise<void>
  setInput: (value: string) => void
  clearError: () => void
  connect: () => Promise<void>
  beginListening: () => Promise<void>
  endListening: () => Promise<void>
  submit: (text: string, source?: 'text' | 'voice') => Promise<void>
  approve: (action: ApprovalAction) => Promise<void>
  stopSpeaking: () => Promise<void>
  startRecording: () => Promise<void>
  stopRecording: () => Promise<void>
  downloadRecording: () => void
  triggerProximityGreeting: (detection: ProximityDetection) => Promise<boolean>
  openArtifact: () => void
}

function getConversationId(): string {
  const key = 'smartoffice_voice_conversation_id'
  const existing = sessionStorage.getItem(key)
  if (existing) return existing
  const value = `voice-${crypto.randomUUID()}`
  sessionStorage.setItem(key, value)
  return value
}

function runtimeInitial(): RealtimeRuntimeStatus {
  return {
    connected: false,
    connectionState: 'not-created',
    dataChannelState: 'not-created',
    microphoneAttached: false,
    responseActive: false,
    outputActive: false,
  }
}

function utteranceLanguage(text: string, selected: VoiceLanguage): VoiceLanguage {
  return CJK.test(text) ? 'zh' : /[A-Za-z]/.test(text) ? 'en' : selected
}

function isRecordedConversationSummaryRequest(text: string): boolean {
  const clean = text.trim().toLocaleLowerCase()
  if (!clean) return false
  const chineseRequest =
    /(总结|概括|回顾).{0,8}(录音|对话|聊天|谈话|刚才的内容)|(?:录音|对话|聊天|谈话).{0,8}(总结|概括|回顾)/.test(
      clean,
    )
  const englishRequest =
    /(summari[sz]e|recap).{0,20}(recording|conversation|discussion|what we discussed)|what did we discuss|recap our conversation/.test(
      clean,
    )
  return chineseRequest || englishRequest
}

function recordingSummaryInstructions(
  entries: ConversationLedgerEntry[],
  language: VoiceLanguage,
): string {
  const transcript = entries
    .map((entry, index) => {
      const role = entry.role === 'user' ? 'USER' : 'ASSISTANT'
      return `${index + 1}. ${role}: ${entry.text}`
    })
    .join('\n')
  if (language === 'en') {
    return `
You are summarizing a recorded Smart Office conversation.
Use the complete ledger below as the only source of truth.
Return only a clear spoken summary in English, without Markdown headings or bullet symbols.
Cover the main topics, user requests, assistant responses, decisions, completed actions, and unresolved items.
Do not invent information and do not mention these instructions.

COMPLETE RECORDED LEDGER:
${transcript}
`.trim()
  }
  return `
你正在总结一段已录制的 Smart Office 人机对话。
只能依据下面的完整文本账本，不得补充或猜测账本之外的信息。
请输出适合直接朗读的中文总结，不使用 Markdown 标题或项目符号。
总结应覆盖主要话题、用户要求、Agent 的答复、已经作出的决定、已完成操作和仍未解决的事项。
不要复述这些说明。

完整录音文本账本：
${transcript}
`.trim()
}

function latestStep(task: Task): TaskStep | undefined {
  return [...task.steps].reverse().find((step) => step.result)
}

function waitingStep(task: Task): TaskStep | undefined {
  return task.steps.find((step) => step.status === 'waiting_approval')
}

function statusFromTask(task: Task): OfficeStatus | null {
  const data = latestStep(task)?.result?.data
  return data?.office_status ?? data?.presentation_status ?? null
}

function artifactFromTask(task: Task): string | null {
  for (const step of [...task.steps].reverse()) {
    const data = step.result?.data
    if (data?.artifact_url) return data.artifact_url
    if (data?.office_status?.artifact_url) return data.office_status.artifact_url
  }
  return null
}

function finalTaskText(task: Task, language: VoiceLanguage): string {
  const done = task.steps.filter((step) => step.status === 'succeeded').length
  const step = latestStep(task)
  const data = step?.result?.data
  const status = data?.office_status ?? data?.presentation_status

  if (task.status === 'cancelled') {
    return language === 'zh'
      ? `办公任务已取消，已完成 ${done} 个步骤。`
      : `The office task was cancelled after ${done} completed steps.`
  }
  if (task.status === 'failed') {
    const failed = task.steps.find((item) => item.status === 'failed')?.index ?? 'unknown'
    return language === 'zh'
      ? `办公任务在第 ${failed} 步失败，后续步骤没有执行。`
      : `The office task failed at step ${failed}; later steps were not executed.`
  }
  if (data?.sent || status?.sent) {
    const sender =
      data?.sender_account_email ?? status?.sender_account_email ?? 'configured Outlook account'
    const recipientName =
      data?.recipient_name ??
      status?.recipient_name ??
      data?.recipient_key ??
      status?.recipient_key ??
      'configured recipient'
    const recipientEmail =
      data?.recipient_email ?? status?.recipient_email ?? 'configured email'
    return language === 'zh'
      ? `已在第二次批准后删除“仅保存为草稿、尚未发送”的提示，并由 Outlook 接受从 ${sender} 发往 ${recipientName}（${recipientEmail}）的发送操作。最终送达状态由 Outlook 和网络处理。`
      : `After the second approval, the draft-only notice was removed and Outlook accepted the send from ${sender} to ${recipientName} (${recipientEmail}). Final delivery is handled by Outlook and the network.`
  }
  if (data?.outlook_draft_created || status?.outlook_draft_created) {
    const sender =
      data?.sender_account_email ?? status?.sender_account_email ?? 'configured Outlook account'
    const recipientName =
      data?.recipient_name ??
      status?.recipient_name ??
      data?.recipient_key ??
      status?.recipient_key ??
      'configured recipient'
    const recipientEmail =
      data?.recipient_email ?? status?.recipient_email ?? 'configured email'
    return language === 'zh'
      ? `Outlook 草稿已创建并验证。发件账号为 ${sender}，收件人为 ${recipientName}（${recipientEmail}），邮件尚未发送。`
      : `A verified Outlook draft was created from ${sender} for ${recipientName} (${recipientEmail}); it has not been sent.`
  }
  const summary = data?.summary_path_relative ?? status?.summary_path_relative
  if (summary) {
    return language === 'zh'
      ? `演示摘要已生成：${summary}。`
      : `The presentation summary was generated at ${summary}.`
  }
  if (status?.volume_percent !== undefined || status?.brightness_percent !== undefined) {
    return language === 'zh'
      ? `办公任务已完成。当前音量 ${status?.volume_percent ?? '不可用'}%，亮度 ${status?.brightness_percent ?? '不可用'}%。`
      : `The office task completed. Volume is ${status?.volume_percent ?? 'unavailable'}% and brightness is ${status?.brightness_percent ?? 'unavailable'}%.`
  }
  if (status?.slideshow_active) {
    return language === 'zh'
      ? `任务已完成。当前第 ${status.current_slide} 页，共 ${status.total_slides} 页。`
      : `The task completed. The slide show is on slide ${status.current_slide} of ${status.total_slides}.`
  }
  return language === 'zh'
    ? `已完成并验证 ${task.steps.length} 个办公步骤。`
    : `Completed and verified ${task.steps.length} office steps.`
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

async function postJson<T>(url: string, body: unknown): Promise<T | null> {
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(body),
    })
    if (!response.ok) return null
    return (await response.json()) as T
  } catch {
    return null
  }
}

export function useOfficeVoiceController(): OfficeVoiceController {
  const browser = useRef(new BrowserSpeechCapture())
  const audioRecorder = useRef(new ConversationAudioRecorder())
  const generation = useRef(0)
  const approvalPrompted = useRef<string | null>(null)
  const conversationIdRef = useRef(getConversationId())
  const recordingActiveRef = useRef(false)
  const recordingSessionExistsRef = useRef(false)
  const recordingStartedAtRef = useRef(0)
  const recordingLedgerRef = useRef<ConversationLedgerEntry[]>([])
  const skipAssistantLedgerTextRef = useRef<string | null>(null)
  const [conversationPhase, setConversationPhase] = useState<ConversationPhase>('standby')
  const [language, setLanguageState] = useState<VoiceLanguage>('zh')
  const [actor, setActorState] = useState<OfficeActor>(
    (localStorage.getItem('smartoffice_actor_type') as OfficeActor) || 'visitor',
  )
  const [asr, setAsrState] = useState<OfficeAsrProvider>(
    localStorage.getItem('smartoffice_asr_provider') === 'browser' ? 'browser' : 'realtime',
  )
  const [voice, setVoiceState] = useState<VoiceOutputProvider>(
    voiceOutputManager.selectedProvider(),
  )
  const [panel, setPanel] = useState<OfficePanelState>('idle')
  const [runtime, setRuntime] = useState<RealtimeRuntimeStatus>(runtimeInitial)
  const [input, setInput] = useState('')
  const [transcript, setTranscript] = useState('')
  const [answer, setAnswer] = useState('')
  const [route, setRoute] = useState('')
  const [permission, setPermission] = useState('')
  const [tool, setTool] = useState('')
  const [verified, setVerified] = useState<boolean | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskStatus, setTaskStatus] = useState<OfficeTaskStatus | null>(null)
  const [pendingApprovalTool, setPendingApprovalTool] = useState<string | null>(null)
  const [pendingRecipientKey, setPendingRecipientKey] = useState<string | null>(null)
  const [office, setOffice] = useState<OfficeStatus | null>(null)
  const [contentUrl, setContentUrl] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [recordingActive, setRecordingActive] = useState(false)
  const [recordingAvailable, setRecordingAvailable] = useState(false)
  const [recordingDurationSeconds, setRecordingDurationSeconds] = useState(0)
  const [recordingTurnCount, setRecordingTurnCount] = useState(0)
  const [recordingError, setRecordingError] = useState('')

  const listening = panel === 'listening'
  const busy = ['connecting', 'processing', 'speaking'].includes(panel)
  const active = Boolean(
    taskId && taskStatus && !['completed', 'failed', 'cancelled'].includes(taskStatus),
  )
  const browserAsrAvailable = browser.current.available()

  useEffect(() => {
    const timer = window.setInterval(() => setRuntime(realtimeAgent.status()), 500)
    return () => {
      window.clearInterval(timer)
      generation.current += 1
    }
  }, [])

  useEffect(() => {
    if (!recordingActive) return
    const updateDuration = () => {
      setRecordingDurationSeconds(
        Math.max(0, Math.floor((Date.now() - recordingStartedAtRef.current) / 1_000)),
      )
    }
    updateDuration()
    const timer = window.setInterval(updateDuration, 1_000)
    return () => window.clearInterval(timer)
  }, [recordingActive])

  useEffect(() => {
    const clean = answer.trim()
    if (!clean || !recordingActiveRef.current) return
    if (skipAssistantLedgerTextRef.current === clean) {
      skipAssistantLedgerTextRef.current = null
      return
    }
    appendRecordingTurn('assistant', clean)
  }, [answer])

  useEffect(() => {
    return () => {
      recordingActiveRef.current = false
      void audioRecorder.current.dispose()
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const refresh = async () => {
      try {
        const query = new URLSearchParams({ language, actor_type: actor })
        const response = await fetch(
          `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(conversationIdRef.current)}?${query}`,
          { headers: { Accept: 'application/json' } },
        )
        if (!response.ok) return
        const payload = (await response.json()) as ConversationEnvelope
        const next = payload.state?.conversation_phase
        if (!cancelled && next) setConversationPhase(next)
      } catch {
        // Conversation memory must never block the core Office workflow.
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), CONVERSATION_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [actor, language])

  function persistRecordingLedger(): void {
    try {
      sessionStorage.setItem(
        `smartoffice_recording_ledger_${conversationIdRef.current}`,
        JSON.stringify(recordingLedgerRef.current),
      )
    } catch {
      // The in-memory ledger remains authoritative if browser storage is unavailable.
    }
  }

  function appendRecordingTurn(
    role: ConversationLedgerEntry['role'],
    text: string,
    summarisable = true,
  ): void {
    if (!recordingActiveRef.current) return
    const clean = text.trim()
    if (!clean) return
    const previous = recordingLedgerRef.current.at(-1)
    if (previous?.role === role && previous.text === clean) return
    const entry: ConversationLedgerEntry = {
      entryId: crypto.randomUUID(),
      role,
      text: clean,
      timestamp: Date.now(),
      summarisable,
    }
    recordingLedgerRef.current = [...recordingLedgerRef.current, entry]
    setRecordingTurnCount(recordingLedgerRef.current.length)
    persistRecordingLedger()
  }

  async function startRecording(): Promise<void> {
    if (recordingActiveRef.current) return
    setRecordingError('')
    try {
      await audioRecorder.current.start()
      recordingLedgerRef.current = []
      recordingSessionExistsRef.current = true
      recordingActiveRef.current = true
      recordingStartedAtRef.current = Date.now()
      setRecordingTurnCount(0)
      setRecordingDurationSeconds(0)
      setRecordingAvailable(true)
      setRecordingActive(true)
      try {
        sessionStorage.removeItem(`smartoffice_recording_ledger_${conversationIdRef.current}`)
      } catch {
        // Optional browser persistence must not block recording.
      }
    } catch (errorValue) {
      recordingActiveRef.current = false
      recordingSessionExistsRef.current = false
      setRecordingActive(false)
      setRecordingAvailable(false)
      setRecordingError(errorText(errorValue))
    }
  }

  async function stopRecording(): Promise<void> {
    if (!recordingActiveRef.current) return
    setRecordingError('')
    recordingActiveRef.current = false
    setRecordingActive(false)
    try {
      const result = await audioRecorder.current.stop()
      setRecordingAvailable(Boolean(result?.blob.size))
      if (result) setRecordingDurationSeconds(result.durationSeconds)
    } catch (errorValue) {
      setRecordingAvailable(false)
      setRecordingError(errorText(errorValue))
    }
  }

  function downloadRecording(): void {
    if (!audioRecorder.current.download()) {
      setRecordingError(
        language === 'zh' ? '当前没有已完成的录音可供下载。' : 'No completed recording is available.',
      )
    }
  }

  async function handleRecordedConversationSummary(
    clean: string,
    selectedLanguage: VoiceLanguage,
  ): Promise<boolean> {
    if (!isRecordedConversationSummaryRequest(clean)) return false
    setPanel('processing')
    setError('')
    setRoute('recording_summary')
    setPermission('not_required')
    setTool('')
    setVerified(null)

    if (recordingActiveRef.current) appendRecordingTurn('user', clean, false)

    let responseText: string
    const sourceEntries = recordingLedgerRef.current.filter((entry) => entry.summarisable)
    if (!recordingSessionExistsRef.current) {
      responseText =
        selectedLanguage === 'zh'
          ? '无法完成对话总结，因为当前没有可用的录音。请先点击录音按钮开始录音。'
          : 'I cannot summarize the conversation because there is no available recording. Please start recording first.'
      setRoute('recording_summary_unavailable')
    } else if (
      sourceEntries.length === 0 ||
      !sourceEntries.some((entry) => entry.role === 'user') ||
      !sourceEntries.some((entry) => entry.role === 'assistant')
    ) {
      responseText =
        selectedLanguage === 'zh'
          ? '录音中还没有完整的人机对话，因此目前没有可总结的内容。'
          : 'The recording does not yet contain a complete human-agent exchange to summarize.'
      setRoute('recording_summary_empty')
    } else {
      responseText = await realtimeAgent.generateText(
        recordingSummaryInstructions(sourceEntries, selectedLanguage),
        selectedLanguage,
        'recorded_conversation_summary',
      )
      if (!responseText.trim()) {
        throw new Error(
          selectedLanguage === 'zh'
            ? '对话总结模型没有返回内容。'
            : 'The conversation summary model returned no content.',
        )
      }
    }

    skipAssistantLedgerTextRef.current = responseText
    setAnswer(responseText)
    if (recordingActiveRef.current) appendRecordingTurn('assistant', responseText, false)
    setConversationPhase('awaiting_user')
    await speak(responseText, selectedLanguage)
    return true
  }

  function fail(errorValue: unknown): void {
    setError(errorText(errorValue))
    setPanel('error')
  }

  async function beginConversationTurn(
    text: string,
    selectedLanguage: VoiceLanguage,
    source: 'text' | 'voice',
  ): Promise<void> {
    setConversationPhase('engaged')
    await postJson(
      `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(conversationIdRef.current)}/turn-start`,
      {
        language: selectedLanguage,
        actor_type: actor,
        text,
        source,
      },
    )
  }

  async function completeConversationTurn(
    text: string,
    turnRoute: string,
    activeTaskId: string | null = null,
  ): Promise<void> {
    const nextPhase: ConversationPhase = activeTaskId ? 'task_active' : 'awaiting_user'
    setConversationPhase(nextPhase)
    await postJson(
      `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(conversationIdRef.current)}/turn-complete`,
      {
        text,
        route: turnRoute,
        task_id: activeTaskId,
        expect_reply: activeTaskId === null,
        source: 'virtual_host',
      },
    )
  }

  async function setConversationTaskState(
    id: string | null,
    isActive: boolean,
    finalText = '',
  ): Promise<void> {
    setConversationPhase(isActive ? 'task_active' : 'awaiting_user')
    await postJson(
      `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(conversationIdRef.current)}/task-state`,
      {
        task_id: id,
        active: isActive,
        final_text: finalText,
      },
    )
  }

  async function speak(text: string, selectedLanguage: VoiceLanguage): Promise<void> {
    if (voice === 'none') {
      setPanel('idle')
      return
    }
    setPanel('speaking')
    try {
      await voiceOutputManager.speak(text, selectedLanguage)
      setPanel('idle')
    } catch (errorValue) {
      if (errorValue instanceof Error && errorValue.name === 'AbortError') {
        setPanel('idle')
        return
      }
      throw errorValue
    }
  }

  async function connect(): Promise<void> {
    setPanel('connecting')
    setError('')
    try {
      await realtimeAgent.prewarm(language)
      setRuntime(realtimeAgent.status())
      setPanel('idle')
    } catch (errorValue) {
      fail(errorValue)
    }
  }

  async function beginListening(): Promise<void> {
    setError('')
    setTranscript('')
    setAnswer('')
    setConversationPhase('engaged')
    try {
      await voiceOutputManager.stop()
      if (asr === 'realtime') await realtimeAgent.beginCapture(language)
      else await browser.current.begin(language, setTranscript)
      setPanel('listening')
    } catch (errorValue) {
      fail(errorValue)
    }
  }

  async function endListening(): Promise<void> {
    setPanel('processing')
    try {
      const text =
        asr === 'realtime' ? await realtimeAgent.endCapture() : await browser.current.end()
      setTranscript(text)
      await performSubmit(text, 'voice')
    } catch (errorValue) {
      fail(errorValue)
    }
  }

  async function monitor(
    id: string,
    selectedLanguage: VoiceLanguage,
    token: number,
  ): Promise<void> {
    const deadline = performance.now() + TASK_TIMEOUT
    while (performance.now() < deadline && generation.current === token) {
      const response = await fetch(`${OFFICE_API_BASE}/agent/tasks/${encodeURIComponent(id)}`)
      if (!response.ok) throw new Error(`Task status failed: ${response.status}`)
      const task = (await response.json()) as Task
      if (generation.current !== token) return

      setTaskStatus(task.status)
      const state = statusFromTask(task)
      if (state) setOffice(state)
      const step = latestStep(task)
      if (step?.result) {
        setTool(step.result.tool_name)
        setVerified(step.result.data?.verification?.ok ?? null)
      }
      const artifact = artifactFromTask(task)
      if (artifact) setContentUrl(artifact)

      const waiting = waitingStep(task)
      if (task.status === 'waiting_approval' && waiting) {
        setConversationPhase('task_active')
        const recipientKey = waiting.args?.recipient_key ?? null
        const approvalKey = `${id}:${waiting.index}:${waiting.tool_name ?? 'unknown'}:${recipientKey ?? 'latest'}`
        setPendingApprovalTool(waiting.tool_name ?? null)
        setPendingRecipientKey(recipientKey)
        if (approvalPrompted.current !== approvalKey) {
          approvalPrompted.current = approvalKey
          const isSend = waiting.tool_name === 'outlook_send_approved_draft'
          const recipientLabel = recipientKey
            ? `联系人 ${recipientKey}`
            : '最新已验证草稿的联系人'
          const prompt =
            selectedLanguage === 'zh'
              ? isSend
                ? `发送给${recipientLabel}的邮件需要第二次批准。批准后会先删除正文中的“该邮件目前仅保存为 Outlook 草稿，尚未发送。”，重新保存并核对发件人与白名单收件人，然后调用 Outlook 发送。请检查草稿后再批准。`
                : `为${recipientLabel}创建本机 Outlook 草稿需要第一次批准。草稿会使用已登录的 Outlook 账号，但此时不会发送。`
              : isSend
                ? `Sending to ${recipientLabel} requires a second approval. The draft-only notice will be removed and the allowlisted recipient will be re-verified before Outlook Send() is invoked.`
                : `Creating the Outlook draft for ${recipientLabel} requires the first approval. It will not send at this stage.`
          setAnswer(prompt)
          if (!realtimeAgent.status().microphoneAttached) await speak(prompt, selectedLanguage)
        }
      }

      if (['completed', 'failed', 'cancelled'].includes(task.status)) {
        setPendingApprovalTool(null)
        setPendingRecipientKey(null)
        const text = finalTaskText(task, selectedLanguage)
        setAnswer(text)
        await setConversationTaskState(id, false, text)
        await completeConversationTurn(text, 'office_task_complete')
        if (!realtimeAgent.status().microphoneAttached && task.status !== 'cancelled') {
          await speak(text, selectedLanguage)
        }
        return
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250))
    }
    if (generation.current === token) {
      throw new Error('The office task did not finish within three minutes.')
    }
  }

  async function performSubmit(text: string, source: 'text' | 'voice'): Promise<void> {
    const clean = text.trim()
    if (!clean) throw new Error('请输入文字或完成一次语音识别。')

    setPanel('processing')
    setError('')
    const selectedLanguage = utteranceLanguage(clean, language)
    if (await handleRecordedConversationSummary(clean, selectedLanguage)) return

    appendRecordingTurn('user', clean)
    await beginConversationTurn(clean, selectedLanguage, source)
    const decision = await realtimeOfficeInterpreter.interpret(clean, selectedLanguage)

    if (decision.kind === 'clarify') {
      const clarification =
        decision.clarification ||
        (selectedLanguage === 'zh'
          ? '请明确办公操作或选择已配置的邮件联系人。'
          : 'Please clarify the office action or choose a configured email recipient.')
      setAnswer(clarification)
      await completeConversationTurn(clarification, 'clarification')
      await speak(clarification, selectedLanguage)
      return
    }

    if (active && decision.kind === 'tool_call') {
      const activeMessage =
        selectedLanguage === 'zh'
          ? '当前已有办公任务正在执行。请先等待、批准、跳过或取消。'
          : 'An office task is already active. Wait, approve, skip, or cancel it first.'
      setAnswer(activeMessage)
      await completeConversationTurn(activeMessage, 'active_task_guard', taskId)
      await speak(activeMessage, selectedLanguage)
      return
    }

    const call = decision.kind === 'tool_call' ? decision.toolCall : null
    const endpoint = call?.name === 'office_plan' ? '/agent/office-turn' : '/agent/turn'
    const response = await fetch(`${OFFICE_API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: conversationIdRef.current,
        text: clean,
        language: selectedLanguage,
        input_source: source,
        actor_context: { type: actor, source: 'shared_office_voice_controller' },
        active_task_id: active ? taskId : null,
        realtime_tool_call: call,
      }),
    })
    if (!response.ok) {
      throw new Error(`Agent turn failed: ${response.status} ${await response.text()}`)
    }

    const payload = (await response.json()) as Turn
    const safe =
      selectedLanguage === 'en' && CJK.test(payload.spoken_text)
        ? 'The office request was processed. Check the verified status shown on screen.'
        : payload.spoken_text
    setRoute(payload.route)
    setPermission(payload.permission_decision)
    setAnswer(safe)
    setTool(payload.tool_result?.tool_name ?? call?.name ?? '')
    setVerified(payload.verification_result?.ok ?? null)
    setOffice(payload.office_status ?? payload.presentation_status ?? null)
    setContentUrl(payload.content_url)
    if (payload.task_id) setTaskId(payload.task_id)
    if (payload.task_status) setTaskStatus(payload.task_status as OfficeTaskStatus)

    if (payload.route === 'office_planned_task' && payload.task_id && payload.tool_result?.ok) {
      const token = generation.current + 1
      generation.current = token
      await completeConversationTurn(safe, payload.route, payload.task_id)
      await setConversationTaskState(payload.task_id, true)
      await speak(safe, selectedLanguage)
      void monitor(payload.task_id, selectedLanguage, token).catch(fail)
      return
    }
    await completeConversationTurn(safe, payload.route)
    await speak(safe, selectedLanguage)
  }

  async function submit(text: string, source: 'text' | 'voice' = 'text'): Promise<void> {
    try {
      await performSubmit(text, source)
    } catch (errorValue) {
      fail(errorValue)
    }
  }

  async function approve(action: ApprovalAction): Promise<void> {
    if (!taskId) return
    try {
      if (action === 'cancel') {
        const response = await fetch(`${OFFICE_API_BASE}/agent/tasks/${taskId}/cancel`, {
          method: 'POST',
        })
        if (!response.ok) {
          throw new Error(`Cancel failed: ${response.status} ${await response.text()}`)
        }
        setAnswer('已请求取消当前任务。')
        return
      }

      const response = await fetch(`${OFFICE_API_BASE}/agent/tasks/${taskId}/approval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          note: 'Submitted from shared Office voice controller',
        }),
      })
      if (!response.ok) {
        throw new Error(`Approval failed: ${response.status} ${await response.text()}`)
      }
      const isSend = pendingApprovalTool === 'outlook_send_approved_draft'
      const recipientText = pendingRecipientKey ? `联系人 ${pendingRecipientKey}` : '所选联系人'
      setAnswer(
        action === 'approve'
          ? isSend
            ? `已完成第二次批准，正在删除草稿提示并向${recipientText}发送。`
            : `已完成第一次批准，正在为${recipientText}创建 Outlook 草稿。`
          : isSend
            ? '已跳过 Outlook 发送步骤，草稿保持未发送。'
            : '已跳过 Outlook 草稿步骤。',
      )
    } catch (errorValue) {
      fail(errorValue)
    }
  }

  async function stopSpeaking(): Promise<void> {
    try {
      await voiceOutputManager.stop()
      setPanel('idle')
    } catch (errorValue) {
      fail(errorValue)
    }
  }

  async function triggerProximityGreeting(detection: ProximityDetection): Promise<boolean> {
    if (
      conversationPhase !== 'standby' ||
      panel !== 'idle' ||
      active ||
      listening ||
      runtime.outputActive ||
      runtime.microphoneAttached
    ) {
      return false
    }

    const payload = await postJson<ProximityGreetingEnvelope>(
      `${OFFICE_API_BASE}/api/conversations/${encodeURIComponent(conversationIdRef.current)}/proximity-greeting`,
      {
        language,
        actor_type: actor,
        ...detection,
      },
    )
    if (!payload?.triggered || !payload.greeting) return false

    setConversationPhase(payload.conversation_phase ?? 'awaiting_user')
    setTranscript('')
    setAnswer(payload.greeting)
    setRoute('proximity_greeting')
    try {
      await speak(payload.greeting, language)
      return true
    } catch (errorValue) {
      fail(errorValue)
      return false
    }
  }

  function setActor(value: OfficeActor): void {
    setActorState(value)
    localStorage.setItem('smartoffice_actor_type', value)
  }

  function setAsr(value: OfficeAsrProvider): void {
    setAsrState(value)
    localStorage.setItem('smartoffice_asr_provider', value)
  }

  async function setVoice(value: VoiceOutputProvider): Promise<void> {
    try {
      await voiceOutputManager.setProvider(value)
      setVoiceState(value)
    } catch (errorValue) {
      fail(errorValue)
    }
  }

  function openArtifact(): void {
    if (!contentUrl) return
    window.open(`${OFFICE_API_BASE}${contentUrl}`, '_blank', 'noopener,noreferrer')
  }

  return {
    conversationId: conversationIdRef.current,
    conversationPhase,
    language,
    actor,
    asr,
    voice,
    panel,
    runtime,
    input,
    transcript,
    answer,
    route,
    permission,
    tool,
    verified,
    taskId,
    taskStatus,
    pendingApprovalTool,
    pendingRecipientKey,
    office,
    contentUrl,
    error,
    listening,
    busy,
    active,
    browserAsrAvailable,
    recordingActive,
    recordingAvailable,
    recordingDurationSeconds,
    recordingTurnCount,
    recordingError,
    setLanguage: setLanguageState,
    setActor,
    setAsr,
    setVoice,
    setInput,
    clearError: () => setError(''),
    connect,
    beginListening,
    endListening,
    submit,
    approve,
    stopSpeaking,
    startRecording,
    stopRecording,
    downloadRecording,
    triggerProximityGreeting,
    openArtifact,
  }
}
