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

type AsrProvider = 'realtime' | 'browser'
type ActorType = 'visitor' | 'employee' | 'operator'
type PanelState = 'idle' | 'connecting' | 'listening' | 'processing' | 'speaking' | 'error'

type PresentationStatus = {
  presentation_open?: boolean
  slideshow_active?: boolean
  current_slide?: number | null
  total_slides?: number | null
  target_monitor_device?: string | null
  slideshow_monitor_device?: string | null
  monitor_placement_enforced?: boolean
}

type TurnResponse = {
  conversation_id: string
  route: string
  normalized_text: string
  spoken_text: string
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

export default function VoiceDebugPanel() {
  const browserCaptureRef = useRef(new BrowserSpeechCapture())
  const [language, setLanguage] = useState<VoiceLanguage>('zh')
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
  const [textInput, setTextInput] = useState('请打开演示文稿')
  const [answer, setAnswer] = useState('')
  const [lastRoute, setLastRoute] = useState('')
  const [lastScene, setLastScene] = useState('')
  const [permissionDecision, setPermissionDecision] = useState('')
  const [sourceIds, setSourceIds] = useState<string[]>([])
  const [contentUrl, setContentUrl] = useState<string | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [lastToolName, setLastToolName] = useState('')
  const [verificationPassed, setVerificationPassed] = useState<boolean | null>(null)
  const [presentationStatus, setPresentationStatus] = useState<PresentationStatus | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(true)

  const listening = panelState === 'listening'
  const busy = ['connecting', 'processing', 'speaking'].includes(panelState)

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

  async function submitTurn(text: string, inputSource: 'text' | 'voice'): Promise<void> {
    const clean = text.trim()
    if (!clean) {
      setError('请输入文字或完成一次语音识别。')
      setPanelState('error')
      return
    }

    setError('')
    setPanelState('processing')
    const decision = await realtimePresentationInterpreter.interpret(clean, language)
    if (decision.kind === 'clarify') {
      const clarification = decision.clarification || (language === 'zh' ? '请明确您要执行的演示操作。' : 'Please clarify the presentation action.')
      setAnswer(clarification)
      setLastRoute('clarification')
      setLastScene('office')
      setPermissionDecision('not_required')
      if (voiceProvider === 'none') {
        setPanelState('idle')
        return
      }
      setPanelState('speaking')
      await voiceOutputManager.speak(clarification, language)
      setPanelState('idle')
      return
    }

    const realtimeToolCall = decision.kind === 'tool_call' ? decision.toolCall : null
    const response = await fetch(`${API_BASE_URL}/agent/turn`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: conversationId(),
        text: clean,
        language,
        input_source: inputSource,
        actor_context: { type: actorType, source: 'phase3_gate2a_panel' },
        realtime_tool_call: realtimeToolCall,
      }),
    })
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw new Error(`Agent turn failed: ${response.status} ${detail}`)
    }

    const payload = (await response.json()) as TurnResponse
    setLastRoute(payload.route)
    setLastScene(payload.scene)
    setPermissionDecision(payload.permission_decision)
    setSourceIds(payload.source_ids ?? [])
    setContentUrl(payload.content_url)
    setTaskId(payload.task_id)
    setAnswer(payload.spoken_text)
    setLastToolName(payload.tool_result?.tool_name ?? payload.realtime_tool_call?.name ?? '')
    setVerificationPassed(payload.verification_result?.ok ?? null)
    if (payload.presentation_status) setPresentationStatus(payload.presentation_status)

    if (voiceProvider === 'none') {
      setPanelState('idle')
      return
    }

    setPanelState('speaking')
    try {
      await voiceOutputManager.speak(payload.spoken_text, language)
      setPanelState('idle')
    } catch (caught) {
      if (caught instanceof Error && caught.name === 'AbortError') {
        setPanelState('idle')
        return
      }
      throw caught
    }
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
        {expanded ? '收起 Gate 2A 控制台' : '打开 Gate 2A 控制台'}
      </button>

      {expanded ? (
        <div className="voice-panel-content">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-kicker">M3A-Fusion · Gate 2A</span>
              <strong>GPT Realtime 语音控制 PowerPoint</strong>
            </div>
            <span className={`voice-state state-${panelState}`}>{stateLabel(panelState)}</span>
          </div>

          <div className="voice-settings-grid">
            <label>
              语言
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
          </div>

          <div className="voice-text-test">
            <input
              value={textInput}
              disabled={listening || busy}
              onChange={(event) => setTextInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !busy && !listening) void submitText()
              }}
              placeholder="输入中文或英文自然语言测试 Gate 2A"
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
            <span>Tool: {lastToolName || '—'}</span>
            <span>
              Verification:{' '}
              {verificationPassed === null ? '—' : verificationPassed ? 'PASS' : 'FAIL'}
            </span>
            <span>Task: {taskId || '—'}</span>
            <span>Sources: {sourceIds.length ? sourceIds.join(', ') : '—'}</span>
            <span>Single voice owner: {voiceProvider}</span>
          </div>

          {error ? <div className="voice-error">{error}</div> : null}
          <p className="voice-safety-note">
            自然语言由 GPT Realtime 解释；Backend 只执行已注册的单步 PowerPoint 能力，并在执行后验证真实页码和副屏位置。Visitor 不能控制 PowerPoint。
          </p>
        </div>
      ) : null}
    </aside>
  )
}
