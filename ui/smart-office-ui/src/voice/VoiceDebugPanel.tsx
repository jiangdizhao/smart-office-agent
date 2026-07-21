import { useEffect, useRef, useState } from 'react'
import { BrowserSpeechCapture } from './browserSpeechRecognition'
import {
  realtimeAgent,
  type RealtimeRuntimeStatus,
  type VoiceLanguage,
} from './realtimeAgentRuntime'
import {
  voiceOutputManager,
  type VoiceOutputProvider,
} from './voiceOutputManager'
import './VoiceDebugPanel.css'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

const ASR_STORAGE_KEY = 'smartoffice_asr_provider'

type AsrProvider = 'realtime' | 'browser'
type PanelState = 'idle' | 'connecting' | 'listening' | 'processing' | 'speaking' | 'error'

type TurnResponse = {
  conversation_id: string
  route: string
  normalized_text: string
  spoken_text: string
  task_id: string | null
  approval_required: boolean
  phase: string
}

function storedAsrProvider(): AsrProvider {
  return localStorage.getItem(ASR_STORAGE_KEY) === 'browser' ? 'browser' : 'realtime'
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
  const [asrProvider, setAsrProvider] = useState<AsrProvider>(storedAsrProvider)
  const [voiceProvider, setVoiceProvider] = useState<VoiceOutputProvider>(
    voiceOutputManager.selectedProvider(),
  )
  const [panelState, setPanelState] = useState<PanelState>('idle')
  const [runtimeStatus, setRuntimeStatus] = useState<RealtimeRuntimeStatus>(
    initialRuntimeStatus,
  )
  const [transcript, setTranscript] = useState('')
  const [textInput, setTextInput] = useState('你好')
  const [answer, setAnswer] = useState('')
  const [lastRoute, setLastRoute] = useState('')
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
    const response = await fetch(`${API_BASE_URL}/agent/turn`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({
        conversation_id: conversationId(),
        text: clean,
        language,
        input_source: inputSource,
        actor_context: { type: 'employee', source: 'phase1_debug_panel' },
      }),
    })
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw new Error(`Agent turn failed: ${response.status} ${detail}`)
    }

    const payload = (await response.json()) as TurnResponse
    setLastRoute(payload.route)
    setAnswer(payload.spoken_text)

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

  return (
    <aside className={`voice-debug-panel ${expanded ? 'expanded' : 'collapsed'}`}>
      <button
        type="button"
        className="voice-panel-toggle"
        onClick={() => setExpanded((current) => !current)}
      >
        {expanded ? '收起 Phase 1 语音' : '打开 Phase 1 语音'}
      </button>

      {expanded ? (
        <div className="voice-panel-content">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-kicker">M3A-Fusion · Phase 1</span>
              <strong>语音底座调试</strong>
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
              语音识别
              <select
                value={asrProvider}
                disabled={listening || busy}
                onChange={(event) => changeAsrProvider(event.target.value as AsrProvider)}
              >
                <option value="realtime">GPT Realtime</option>
                <option
                  value="browser"
                  disabled={!browserCaptureRef.current.available()}
                >
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
              WebRTC: {runtimeStatus.connectionState} / {runtimeStatus.dataChannelState}
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
              placeholder="输入文字测试 /agent/turn"
            />
            <button
              type="button"
              disabled={listening || busy}
              onClick={() => void submitText()}
            >
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

          <div className="voice-diagnostics">
            <span>Route: {lastRoute || '—'}</span>
            <span>Single voice owner: {voiceProvider}</span>
            <span>Browser ASR: {browserCaptureRef.current.available() ? 'available' : 'unavailable'}</span>
          </div>

          {error ? <div className="voice-error">{error}</div> : null}
          <p className="voice-safety-note">
            Provider 失败时只显示错误，不会自动启动第二个声音。
          </p>
        </div>
      ) : null}
    </aside>
  )
}
