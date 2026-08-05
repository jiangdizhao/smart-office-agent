import { useEffect, useMemo, useRef, useState } from 'react'
import { publishSessionMessage } from '../interaction/sessionEventBus'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
import { useProximityGreeting } from '../vision/useProximityGreeting'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import { visitLanguagePreference } from '../voice/visitLanguagePreference'
import {
  useOfficeVoiceController,
  type ConversationPhase,
} from '../voice/useOfficeVoiceController'
import { useVisitLanguageAwareOfficeVoiceController } from '../voice/useVisitLanguageAwareOfficeVoiceController'
import ApprovalOverlay from './ApprovalOverlay'
import LiveCaption from './LiveCaption'
import OperatorDrawer from './OperatorDrawer'
import VirtualHostAvatar, { type VirtualHostVisualState } from './VirtualHostAvatar'
import './VirtualHost.css'
import './VirtualHostPhase3.css'
import './VirtualHostPhase4.css'
import './ConversationRecording.css'
import './ContinuousVoice.css'
import './VirtualHostWarmShowroom.css'

const ACTIVE_TASK_STATUSES = ['created', 'planning', 'running']
type VadUiState = 'idle' | 'listening' | 'processing'

function stateText(
  visualState: VirtualHostVisualState,
  conversationPhase: ConversationPhase,
): string {
  if (visualState === 'idle') {
    const idleLabels: Record<ConversationPhase, string> = {
      standby: 'READY · 就绪',
      engaged: 'CONVERSATION ACTIVE · 对话进行中',
      awaiting_user: 'YOUR TURN · 等待您继续',
      task_active: 'WORKING · 正在处理任务',
      closing: 'CLOSING · 本次服务即将结束',
    }
    return idleLabels[conversationPhase]
  }
  const labels: Record<VirtualHostVisualState, string> = {
    idle: 'READY · 就绪',
    connecting: 'CONNECTING · 正在连接',
    listening: 'LISTENING · 正在聆听',
    processing: 'THINKING · 正在思考',
    speaking: 'SPEAKING · 正在回答',
    executing: 'WORKING · 正在执行',
    'waiting-approval': 'CONFIRMATION NEEDED · 等待确认',
    error: 'PLEASE RETRY · 请重试',
  }
  return labels[visualState]
}

function publicError(error: string | null, language: VoiceLanguage): string | null {
  if (!error) return null
  const text = error.trim()
  if (!text) return null
  if (/aborted|cancelled|canceled|interrupted|stopped/i.test(text)) return null
  return language === 'zh'
    ? `本轮没有完成：${text}`
    : `This turn was not completed: ${text}`
}

export default function VirtualHostApp() {
  const controller = useVisitLanguageAwareOfficeVoiceController(useOfficeVoiceController())
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [userCaption, setUserCaption] = useState('')
  const [assistantCaption, setAssistantCaption] = useState('')
  const [vadUiState, setVadUiState] = useState<VadUiState>('idle')
  const closeTimerRef = useRef<number | null>(null)
  const proximity = useProximityGreeting({
    onVisitStart: (visit) => {
      controller.beginVisit(visit)
    },
    onVisitEnd: () => {
      controller.endVisit()
    },
  })

  useEffect(() => {
    const unsubscribe = publishSessionMessage.subscribe((message) => {
      if (message.role === 'user') setUserCaption(message.text)
      if (message.role === 'assistant') setAssistantCaption(message.text)
    })
    return unsubscribe
  }, [])

  useEffect(() => {
    const onVadStarted = () => setVadUiState('listening')
    const onVadStopped = () => setVadUiState('processing')
    const onOutputStarted = () => setVadUiState('idle')
    const onOutputCompleted = () => setVadUiState('idle')
    window.addEventListener('smartoffice:realtime-vad-speech-started', onVadStarted)
    window.addEventListener('smartoffice:realtime-vad-speech-stopped', onVadStopped)
    window.addEventListener('smartoffice:assistant-output-started', onOutputStarted)
    window.addEventListener('smartoffice:assistant-output-completed', onOutputCompleted)
    return () => {
      window.removeEventListener('smartoffice:realtime-vad-speech-started', onVadStarted)
      window.removeEventListener('smartoffice:realtime-vad-speech-stopped', onVadStopped)
      window.removeEventListener('smartoffice:assistant-output-started', onOutputStarted)
      window.removeEventListener('smartoffice:assistant-output-completed', onOutputCompleted)
    }
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && drawerOpen) setDrawerOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      if (closeTimerRef.current != null) window.clearTimeout(closeTimerRef.current)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [drawerOpen])

  async function toggleFullscreen(): Promise<void> {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await document.documentElement.requestFullscreen()
  }

  function toggleLanguage(): void {
    visitLanguagePreference.force(
      controller.language === 'zh' ? 'en' : 'zh',
      'header_language_button',
    )
  }

  const visualState = useMemo<VirtualHostVisualState>(() => {
    if (controller.error) return 'error'
    if (controller.approval) return 'waiting-approval'
    if (ACTIVE_TASK_STATUSES.includes(controller.runtime.taskStatus ?? '')) return 'executing'
    return controller.visualState
  }, [controller.approval, controller.error, controller.runtime.taskStatus, controller.visualState])

  const voiceActive = visualState === 'speaking'
  const isWaitingApproval = Boolean(controller.approval)
  const recipientName = controller.approval?.recipientName ?? ''
  const isSendApproval = controller.approval?.kind === 'send'

  function welcomeText(): string {
    return controller.language === 'zh'
      ? '欢迎，我是 Sara。我会英语和中文，靠近后直接说话即可。'
      : 'Approach and speak to begin. I am Sara and I can help in English or Chinese.'
  }

  const continuousStatus = controller.runtime.continuousListening
    ? visualState === 'listening'
      ? 'Listening to the handheld microphone'
      : visualState === 'processing'
        ? 'Understanding your request'
        : visualState === 'speaking'
          ? 'Speak at any time to interrupt Sara'
          : 'Handheld microphone ready — just speak'
    : 'Approach to activate the handheld microphone'

  const shownError = publicError(controller.error, controller.language)

  return (
    <main className={`virtual-host-shell state-${visualState} conversation-${controller.conversationPhase}`}>
      <div className="virtual-host-background" aria-hidden="true">
        <span className="background-glow glow-one" />
        <span className="background-glow glow-two" />
        <span className="background-grid" />
      </div>

      <header className="virtual-host-header">
        <div className="virtual-host-brand">
          <span className="brand-symbol" aria-hidden="true">SO</span>
          <div>
            <strong>Smart Office</strong>
            <span>Digital Manager · Solution Consultant / 数字管理员 · 解决方案顾问</span>
          </div>
        </div>
        <div className="virtual-host-header-actions">
          <span className={`system-ready ${controller.runtime.connected ? 'connected' : ''}`}>
            <i />
            {controller.runtime.connected ? 'VOICE CONNECTED' : 'SYSTEM READY'}
          </span>
          <button type="button" className="header-button language-button" onClick={toggleLanguage} aria-label="Switch language / 切换语言">
            {controller.language === 'zh' ? '中文' : 'EN'}
          </button>
          <button type="button" className="header-button" onClick={() => void toggleFullscreen()} aria-label="Fullscreen / 全屏">⛶</button>
          <button type="button" className="header-button" onClick={() => setDrawerOpen(true)} aria-label="Settings / 设置">⚙</button>
        </div>
      </header>

      <section className="virtual-host-stage">
        <div className="virtual-host-status" aria-live="polite">
          <span className={`status-dot status-${visualState}`} />
          <span>{stateText(visualState, controller.conversationPhase)}</span>
        </div>

        <VirtualHostAvatar state={visualState} />

        <LiveCaption
          state={visualState}
          language={controller.language}
          userText={userCaption}
          assistantText={assistantCaption}
          welcomeText={welcomeText()}
        />

        <div className={`continuous-voice-card ${vadUiState}`} aria-live="polite">
          <i aria-hidden="true" />
          <strong>{continuousStatus}</strong>
        </div>
      </section>

      {shownError ? (
        <button type="button" className="host-error-toast" onClick={controller.clearError}>
          <strong>{controller.language === 'zh' ? '本轮未完成' : 'Turn not completed'}</strong>
          <span>{shownError}</span>
        </button>
      ) : null}

      {isWaitingApproval ? (
        <ApprovalOverlay
          language={controller.language}
          recipientName={recipientName}
          isSendApproval={isSendApproval}
          voiceActive={voiceActive}
          onApprove={() => controller.approve('approve')}
          onSkip={() => controller.approve('skip')}
          onCancelTask={() => controller.approve('cancel')}
          onStopVoice={controller.stopSpeaking}
        />
      ) : null}

      {drawerOpen ? (
        <OperatorDrawer controller={controller} proximity={proximity} onClose={() => setDrawerOpen(false)} />
      ) : null}
    </main>
  )
}
