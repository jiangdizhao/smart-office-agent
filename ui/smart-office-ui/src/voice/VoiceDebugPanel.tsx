import { useEffect, useRef, useState } from 'react'
import { BrowserSpeechCapture } from './browserSpeechRecognition'
import {
  realtimeAgent,
  type RealtimeRuntimeStatus,
  type VoiceLanguage,
} from './realtimeAgentRuntime'
import {
  realtimePresentationInterpreter,
  type RealtimePresentationToolCall,
} from './realtimePresentationInterpreter'
import {
  voiceOutputManager,
  type VoiceOutputProvider,
} from './voiceOutputManager'
import './VoiceDebugPanel.css'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

const ASR_STORAGE_KEY = 'smartoffice_asr_provider'
const ACTOR_STORAGE_KEY = 'smartoffice_actor_type'
const CJK_PATTERN = /[\u3400-\u9fff]/
const CJK_GLOBAL_PATTERN = /[\u3400-\u9fff]/g
const ENGLISH_WORD_PATTERN = /[A-Za-z]+(?:['’-][A-Za-z]+)*/g
const TASK_POLL_INTERVAL_MS = 250
const TASK_TIMEOUT_MS = 45_000

type AsrProvider = 'realtime' | 'browser'
type ActorType = 'visitor' | 'employee' | 'operator'
type PanelState = 'idle' | 'connecting' | 'listening' | 'processing' | 'speaking' | 'error'
type TaskStatus = 'created' | 'planning' | 'running' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled'

type PresentationStatus = {
  presentation_open?: boolean
  slideshow_active?: boolean
  current_slide?: number | null
  total_slides?: number | null
  target_monitor_device?: string | null
  slideshow_monitor_device?: string | null
  monitor_placement_enforced?: boolean
}

type TaskStep = {
  index: number
  title: string
  tool_name?: string | null
  status: string
  message?: string | null
  result?: {
    tool_name: string
    ok: boolean
    message: string
    data?: {
      presentation_status?: PresentationStatus
      verification?: { ok?: boolean; message?: string }
    }
  } | null
}

type TaskSession = {
  task_id: string
  status: TaskStatus
  summary?: string | null
  steps: TaskStep[]
}

type TurnResponse = {
  conversation_id: string
  route: string
  normalized_text: string
  spoken_text: string
  response_language?: VoiceLanguage
  task_id: string | null
  task_status: string | null
  approval_required: boolean
  actor_type: ActorType
  scene: string
  permission_decision: string
  source_ids: string[]
  content_url: string | null
  intent_source?: string | null
  realtime_tool_call?: RealtimePresentationToolCall | null
  tool_result?: { tool_name: string; ok: boolean; message: string } | null
  verification_result?: { ok: boolean; message: string } | null
  presentation_status?: PresentationStatus | null
  phase: string
}

function storedAsrProvider(): AsrProvider {
  return localStorage.getItem(ASR_STORAGE_KEY) === 'browser' ? 'browser' : 'realtime'
}

function storedActorType(): ActorType {
  const value = localStorage.getItem(ACTOR_STORAGE_KEY)
  if (value === 'employee' || value === 'operator') return value
  return 'visitor'
}

function conversationId(): string {
  const key = 'smartoffice_debug_conversation_id'
  const existing = sessionStorage.getItem(key)
  if (existing) return existing
  const value = `debug-${crypto.randomUUID()}`
  sessionStorage.setItem(key, value)
  return value
}

function initialRuntimeStatus(): RealtimeRuntimeStatus {
  return {
    connected: false,
    connectionState: 'not-created',
    dataChannelState: 'not-created',
    microphoneAttached: false,
    responseActive: false,
    outputActive: false,
  }
}

function stateLabel(state: PanelState): string {
  const labels: Record<PanelState, string> = {
    idle: '空闲',
    connecting: '正在连接',
    listening: '正在聆听',
    processing: '正在处理',
    speaking: '正在朗读',
    error: '发生错误',
  }
  return labels[state]
}

function responseLanguageForUtterance(
  text: string,
  selectedLanguage: VoiceLanguage,
): VoiceLanguage {
  const cjkCount = (text.match(CJK_GLOBAL_PATTERN) ?? []).length
  const englishWords = text.match(ENGLISH_WORD_PATTERN) ?? []
  const latinCharacterCount = englishWords.join('').length

  if (cjkCount === 0 && englishWords.length > 0) return 'en'
  if (cjkCount > 0 && englishWords.length >= 3 && latinCharacterCount >= cjkCount * 2) {
    return 'en'
  }
  if (cjkCount > 0) return 'zh'
  return selectedLanguage
}

function englishPresentationFallback(payload: TurnResponse): string {
  const status = payload.presentation_status
  const toolName = payload.tool_result?.tool_name ?? payload.realtime_tool_call?.name

  if (payload.permission_decision === 'denied') {
    return 'A visitor cannot control PowerPoint. Switch to an employee or operator identity and try again.'
  }
  if (payload.tool_result && !payload.tool_result.ok) {
    return `The PowerPoint action was not executed: ${payload.tool_result.message}`
  }
  if (payload.verification_result && !payload.verification_result.ok) {
    return 'PowerPoint received the action, but the observed state did not pass verification.'
  }
  if (toolName === 'presentation_get_status' && status) {
    if (!status.presentation_open) return 'PowerPoint is not currently open.'
    if (status.slideshow_active) {
      return `The slide show is currently on slide ${status.current_slide ?? 'unknown'} of ${status.total_slides ?? 'unknown'}.`
    }
    return `The presentation is open with ${status.total_slides ?? 'an unknown number of'} slides, but the slide show has not started.`
  }
  if (status?.slideshow_active) {
    return `The PowerPoint action completed. The slide show is on slide ${status.current_slide ?? 'unknown'} of ${status.total_slides ?? 'unknown'}.`
  }
  if (payload.route === 'reception_knowledge') {
    return 'The approved English response is temporarily unavailable. Please ask the question again.'
  }
  return 'The response could not be provided entirely in English. Please repeat the request.'
}

function guardTurnAnswer(payload: TurnResponse, language: VoiceLanguage): string {
  const answer = payload.spoken_text.trim()
  if (language === 'en' && CJK_PATTERN.test(answer)) {
    return englishPresentationFallback(payload)
  }
  return answer
}

function guardClarification(text: string, language: VoiceLanguage): string {
  const clean = text.trim()
  if (language === 'en' && CJK_PATTERN.test(clean)) {
    return 'Please clarify the presentation action you want me to perform.'
  }
  if (clean) return clean
  return language === 'zh'
    ? '请明确您要执行的演示操作。'
    : 'Please clarify the presentation action.'
}

function latestPresentationStatus(task: TaskSession): PresentationStatus | null {
  for (let index = task.steps.length - 1; index >= 0; index -= 1) {
    const status = task.steps[index]?.result?.data?.presentation_status
    if (status) return status
  }
  return null
}

function taskFinalAnswer(task: TaskSession, language: VoiceLanguage): string {
  const status = latestPresentationStatus(task)
  const completedSteps = task.steps.filter((step) => step.status === 'succeeded').length

  if (task.status === 'cancelled') {
    return language === 'zh'
      ? `演示任务已取消，已完成 ${completedSteps} 个步骤。`
      : `The presentation task was cancelled after ${completedSteps} completed steps.`
  }

  if (task.status === 'failed') {
    const failedStep = task.steps.find((step) => step.status === 'failed')
    const stepNumber = failedStep?.index ?? 'unknown'
    return language === 'zh'
      ? `演示任务在第 ${stepNumber} 步失败，后续步骤没有执行。请查看界面中的失败信息。`
      : `The presentation task failed at step ${stepNumber}. Later steps were not executed. Check the failure details shown on screen.`
  }

  if (task.status === 'completed') {
    if (status?.slideshow_active) {
      return language === 'zh'
        ? `已完成并验证 ${task.steps.length} 个演示步骤。当前是第 ${status.current_slide ?? '未知'} 页，共 ${status.total_slides ?? '未知'} 页。`
        : `Completed and verified ${task.steps.length} presentation steps. The slide show is on slide ${status.current_slide ?? 'unknown'} of ${status.total_slides ?? 'unknown'}.`
    }
    return language === 'zh'
      ? `已完成并验证 ${task.steps.length} 个演示步骤。`
      : `Completed and verified ${task.steps.length} presentation steps.`
  }

  return language === 'zh' ? '演示任务仍在执行。' : 'The presentation task is still running.'
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

export default function VoiceDebugPanel() {
  const browserCaptureRef = useRef(new BrowserSpeechCapture())
  const [language, setLanguage] = useState<VoiceLanguage>('zh')
  const [responseLanguage, setResponseLanguage] = useState<VoiceLanguage>('zh')
  const [actorType, setActorType] = useState<ActorType>(storedActorType)
  const [asrProvider, setAsrProvider] = useState<AsrProvider>(storedAsrProvider)
  const [voiceProvider, setVoiceProvider] = useState<VoiceOutputProvider>(
    voiceOutputManager.selectedProvider(),
  )
  const [panelState, setPanelState] = useState<PanelState>('idle')
  const [runtimeStatus, setRuntimeStatus] = useState<RealtimeRuntimeStatus>(
    initialRuntimeStatus,
  )
  const [transcript, setTranscript] = useState('')
  const [textInput, setTextInput] = useState('请打开演示文稿，开始播放，然后跳到第五页')
  const [answer, setAnswer] = useState('')
  const [lastRoute, setLastRoute] = useState('')
  const [lastScene, setLastScene] = useState('')
  const [permissionDecision, setPermissionDecision] = useState('')
  const [sourceIds, setSourceIds] = useState<string[]>([])
  const [contentUrl, setContentUrl] = useState<string | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null)
  const [lastToolName, setLastToolName] = useState('')
  const [verificationPassed, setVerificationPassed] = useState<boolean | null>(null)
  const [presentationStatus, setPresentationStatus] = useState<PresentationStatus | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(true)

  const listening = panelState === 'listening'
  const busy = ['connecting', 'processing', 'speaking'].includes(panelState)
  const taskActive = Boolean(taskId && taskStatus && !['completed', 'failed', 'cancelled'].includes(taskStatus))

  useEffect(() => {
    const timer = window.setInterval(() => {
      setRuntimeStatus(realtimeAgent.status())
    }, 500)
    return () => window.clearInterval(timer)
  }, [])

  async function connectRealtime(): Promise<void> {
    setError('')
    setPanelState('connecting')
    try {
      await realtimeAgent.prewarm(language)
      setRuntimeStatus(realtimeAgent.status())
      setPanelState('idle')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setPanelState('error')
    }
  }

  async function beginCapture(): Promise<void> {
    setError('')
    setTranscript('')
    setAnswer('')
    setSourceIds([])
    setContentUrl(null)
    setLastToolName('')
    setVerificationPassed(null)
    try {
      await voiceOutputManager.stop()
      if (asrProvider === 'realtime') {
        await realtimeAgent.beginCapture(language)
      } else {
        await browserCaptureRef.current.begin(language, setTranscript)
      }
      setPanelState('listening')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setPanelState('error')
    }
  }

  async function endCapture(): Promise<void> {
    setError('')
    setPanelState('processing')
    try {
      const finalTranscript =
        asrProvider === 'realtime'
          ? await realtimeAgent.endCapture()
          : await browserCaptureRef.current.end()
      setTranscript(finalTranscript)
      await submitTurn(finalTranscript, 'voice')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setPanelState('error')
    }
  }

  async function waitForTaskCompletion(activeTaskId: string): Promise<TaskSession> {
    const deadline = performance.now() + TASK_TIMEOUT_MS
    while (performance.now() < deadline) {
      const response = await fetch(`${API_BASE_URL}/agent/tasks/${encodeURIComponent(activeTaskId)}`, {
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) {
        const detail = await response.text().catch(() => '')
        throw new Error(`Task status failed: ${response.status} ${detail}`)
      }
      const task = (await response.json()) as TaskSession
      setTaskStatus(task.status)
      const status = latestPresentationStatus(task)
      if (status) setPresentationStatus(status)
      const latestStep = [...task.steps].reverse().find((step) => step.result)
      if (latestStep?.result) {
        setLastToolName(latestStep.result.tool_name)
        setVerificationPassed(latestStep.result.data?.verification?.ok ?? null)
      }
      if (['completed', 'failed', 'cancelled'].includes(task.status)) return task
      await wait(TASK_POLL_INTERVAL_MS)
    }
    throw new Error('The compound presentation task did not finish within 45 seconds.')
  }

  async function cancelActiveTask(): Promise<void> {
    if (!taskId || !taskActive) return
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/agent/tasks/${encodeURIComponent(taskId)}/cancel`, {
        method: 'POST',
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) {
        const detail = await response.text().catch(() => '')
        throw new Error(`Task cancellation failed: ${response.status} ${detail}`)
      }
      const task = (await response.json()) as TaskSession
      setTaskStatus(task.status)
      setAnswer(responseLanguage === 'zh' ? '已请求取消当前演示任务。' : 'Cancellation was requested for the current presentation task.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  async function speakAnswer(text: string, effectiveLanguage: VoiceLanguage): Promise<void> {
    if (voiceProvider === 'none') {
      setPanelState('idle')
      return
    }
    setPanelState('speaking')
    try {
      await voiceOutputManager.speak(text, effectiveLanguage)
      setPanelState('idle')
    } catch (caught) {
      if (caught instanceof Error && caught.name === 'AbortError') {
        setPanelState('idle')
        return
      }
      throw caught
    }
  }

  async function submitTurn(text: string, inputSource: 'text' | 'voice'): Promise<void> {
    const clean = text.trim()
    if (!clean) {
      setError('请输入文字或完成一次语音识别。')
      setPanelState('error')
      return
    }

    setError('')
    setPanelState('processing')
    const effectiveLanguage = responseLanguageForUtterance(clean, language)
    setResponseLanguage(effectiveLanguage)

    const decision = await realtimePresentationInterpreter.interpret(clean, effectiveLanguage)
    if (decision.kind === 'clarify') {
      const clarification = guardClarification(decision.clarification, effectiveLanguage)
      setAnswer(clarification)
      setLastRoute('clarification')
      setLastScene('office')
      setPermissionDecision('not_required')
      await speakAnswer(clarification, effectiveLanguage)
      return
    }

    const realtimeToolCall = decision.kind === 'tool_call' ? decision.toolCall : null
    const response = await fetch(`${API_BASE_URL}/agent/turn`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: conversationId(),
        text: clean,
        language: effectiveLanguage,
        input_source: inputSource,
        actor_context: { type: actorType, source: 'phase3_gate2b_panel' },
        active_task_id: taskActive ? taskId : null,
        realtime_tool_call: realtimeToolCall,
      }),
    })
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw new Error(`Agent turn failed: ${response.status} ${detail}`)
    }

    const payload = (await response.json()) as TurnResponse
    const safeAnswer = guardTurnAnswer(payload, effectiveLanguage)
    setLastRoute(payload.route)
    setLastScene(payload.scene)
    setPermissionDecision(payload.permission_decision)
    setSourceIds(payload.source_ids ?? [])
    setContentUrl(payload.content_url)
    setTaskId(payload.task_id)
    setTaskStatus((payload.task_status as TaskStatus | null) ?? null)
    setAnswer(safeAnswer)
    setLastToolName(payload.tool_result?.tool_name ?? payload.realtime_tool_call?.name ?? '')
    setVerificationPassed(payload.verification_result?.ok ?? null)
    if (payload.presentation_status) setPresentationStatus(payload.presentation_status)

    if (payload.route === 'office_planned_task' && payload.task_id && payload.tool_result?.ok) {
      const task = await waitForTaskCompletion(payload.task_id)
      const finalAnswer = taskFinalAnswer(task, effectiveLanguage)
      setAnswer(finalAnswer)
      await speakAnswer(finalAnswer, effectiveLanguage)
      return
    }

    await speakAnswer(safeAnswer, effectiveLanguage)
  }

  async function submitText(): Promise<void> {
    try {
      setTranscript(textInput)
      await submitTurn(textInput, 'text')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setPanelState('error')
    }
  }

  async function stopOutput(): Promise<void> {
    setError('')
    try {
      await voiceOutputManager.stop()
      setPanelState('idle')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setPanelState('error')
    }
  }

  async function changeVoiceProvider(provider: VoiceOutputProvider): Promise<void> {
    try {
      await voiceOutputManager.setProvider(provider)
      setVoiceProvider(provider)
      setPanelState('idle')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setPanelState('error')
    }
  }

  function changeAsrProvider(provider: AsrProvider): void {
    if (listening) return
    setAsrProvider(provider)
    localStorage.setItem(ASR_STORAGE_KEY, provider)
  }

  function changeActorType(value: ActorType): void {
    if (listening || busy) return
    setActorType(value)
    localStorage.setItem(ACTOR_STORAGE_KEY, value)
  }

  function openReceptionContent(): void {
    if (!contentUrl) return
    window.open(`${API_BASE_URL}${contentUrl}`, '_blank', 'noopener,noreferrer')
  }

  const presentationState = presentationStatus?.slideshow_active
    ? 'Presenting'
    : presentationStatus?.presentation_open
      ? 'Ready'
      : 'Closed'

  return (
    <aside className={`voice-debug-panel ${expanded ? 'expanded' : 'collapsed'}`}>
      <button
        type="button"
        className="voice-panel-toggle"
        onClick={() => setExpanded((current) => !current)}
      >
        {expanded ? '收起 Gate 2B 控制台' : '打开 Gate 2B 控制台'}
      </button>

      {expanded ? (
        <div className="voice-panel-content">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-kicker">M3A-Fusion · Gate 2B</span>
              <strong>GPT Realtime 复合语音控制 PowerPoint</strong>
            </div>
            <span className={`voice-state state-${panelState}`}>{stateLabel(panelState)}</span>
          </div>

          <div className="voice-settings-grid">
            <label>
              语音识别语言
              <select
                value={language}
                disabled={listening || busy}
                onChange={(event) => setLanguage(event.target.value as VoiceLanguage)}
              >
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>
            </label>

            <label>
              身份
              <select
                value={actorType}
                disabled={listening || busy}
                onChange={(event) => changeActorType(event.target.value as ActorType)}
              >
                <option value="visitor">Visitor</option>
                <option value="employee">Employee</option>
                <option value="operator">Operator</option>
              </select>
            </label>

            <label>
              语音识别
              <select
                value={asrProvider}
                disabled={listening || busy}
                onChange={(event) => changeAsrProvider(event.target.value as AsrProvider)}
              >
                <option value="realtime">GPT Realtime</option>
                <option value="browser" disabled={!browserCaptureRef.current.available()}>
                  Browser ASR
                </option>
              </select>
            </label>

            <label>
              语音输出
              <select
                value={voiceProvider}
                disabled={listening || busy}
                onChange={(event) => {
                  void changeVoiceProvider(event.target.value as VoiceOutputProvider)
                }}
              >
                <option value="realtime">GPT Realtime</option>
                <option value="none">仅显示文字</option>
              </select>
            </label>
          </div>

          <div className="voice-connection-row">
            <span>
              Voice WebRTC: {runtimeStatus.connectionState} / {runtimeStatus.dataChannelState}
            </span>
            <span>Mic: {runtimeStatus.microphoneAttached ? 'attached' : 'released'}</span>
            <button
              type="button"
              disabled={listening || busy || runtimeStatus.connected}
              onClick={() => void connectRealtime()}
            >
              {runtimeStatus.connected ? '已连接' : '连接语音'}
            </button>
          </div>

          <div className="voice-ptt-row">
            <button
              type="button"
              className={listening ? 'voice-ptt listening' : 'voice-ptt'}
              disabled={busy}
              onClick={() => void (listening ? endCapture() : beginCapture())}
            >
              {listening ? '结束说话' : '点击说话'}
            </button>
            <button
              type="button"
              className="voice-stop"
              disabled={!runtimeStatus.outputActive && panelState !== 'speaking'}
              onClick={() => void stopOutput()}
            >
              停止朗读
            </button>
            <button
              type="button"
              className="voice-stop"
              disabled={!taskActive}
              onClick={() => void cancelActiveTask()}
            >
              取消当前任务
            </button>
          </div>

          <div className="voice-text-test">
            <input
              value={textInput}
              disabled={listening || busy}
              onChange={(event) => setTextInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !busy && !listening) void submitText()
              }}
              placeholder="输入中文或英文自然语言测试 Gate 2B"
            />
            <button type="button" disabled={listening || busy} onClick={() => void submitText()}>
              发送
            </button>
          </div>

          <div className="voice-result-grid">
            <div>
              <span>识别文本</span>
              <p>{transcript || '—'}</p>
            </div>
            <div>
              <span>Agent 文本</span>
              <p>{answer || '—'}</p>
            </div>
          </div>

          <div className="voice-result-grid">
            <div>
              <span>Presentation</span>
              <p>
                {presentationState} · {presentationStatus?.current_slide ?? '—'} /{' '}
                {presentationStatus?.total_slides ?? '—'}
              </p>
            </div>
            <div>
              <span>Display</span>
              <p>
                {presentationStatus?.slideshow_monitor_device ??
                  presentationStatus?.target_monitor_device ??
                  '\\\\.\\DISPLAY2'}{' '}
                · {presentationStatus?.monitor_placement_enforced ? 'verified' : 'not verified'}
              </p>
            </div>
          </div>

          {contentUrl ? (
            <button type="button" className="voice-content-open" onClick={openReceptionContent}>
              在浏览器内容页打开接待资料
            </button>
          ) : null}

          <div className="voice-diagnostics">
            <span>Actor: {actorType}</span>
            <span>Route: {lastRoute || '—'}</span>
            <span>Scene: {lastScene || '—'}</span>
            <span>Permission: {permissionDecision || '—'}</span>
            <span>Response language: {responseLanguage}</span>
            <span>Tool: {lastToolName || '—'}</span>
            <span>
              Verification:{' '}
              {verificationPassed === null ? '—' : verificationPassed ? 'PASS' : 'FAIL'}
            </span>
            <span>Task: {taskId || '—'}</span>
            <span>Task status: {taskStatus || '—'}</span>
            <span>Sources: {sourceIds.length ? sourceIds.join(', ') : '—'}</span>
            <span>Single voice owner: {voiceProvider}</span>
          </div>

          {error ? <div className="voice-error">{error}</div> : null}
          <p className="voice-safety-note">
            Gate 2B 支持最多八个受控 PowerPoint 步骤。每一步都通过现有 Task Runtime 执行并验证；任一步失败后，后续步骤不会继续。英文问题强制使用英文答复，中文问题允许必要的英文术语。
          </p>
        </div>
      ) : null}
    </aside>
  )
}
