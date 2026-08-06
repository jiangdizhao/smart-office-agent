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
    processing: 'UNDERSTANDING · 正在理解',
    speaking: 'SPEAKING · 正在说明',
    executing: 'WORKING · 正在执行办公任务',
    'waiting-approval': 'CONFIRMATION REQUIRED · 等待确认',
    error: 'PLEASE TRY AGAIN · 请重试',
  }
  return labels[visualState]
}

function controllerVisualState(
  panel: string,
  taskStatus: string | null,
  active: boolean,
): VirtualHostVisualState {
  if (panel === 'error') return 'error'
  if (taskStatus === 'waiting_approval') return 'waiting-approval'
  if (panel === 'connecting') return 'connecting'
  if (panel === 'listening') return 'listening'
  if (panel === 'processing') return 'processing'
  if (panel === 'speaking') return 'speaking'
  if (active || (taskStatus !== null && ACTIVE_TASK_STATUSES.includes(taskStatus))) return 'executing'
  return 'idle'
}

function welcomeText(): string {
  return 'Welcome, I’m Sara. I speak English and Chinese. Approach and speak to begin. · 欢迎，我是 Sara。我会英语和中文，靠近后直接说话即可。'
}

function publicError(message: string, language: VoiceLanguage): string {
  const clean = message.trim()
  if (!clean) return ''
  if (/GPT Realtime|audio did not|response timed out|data channel/i.test(clean)) {
    return language === 'zh'
      ? '语音服务刚才没有顺利完成，系统已经恢复。请直接对着手持麦克风再说一次。'
      : 'The voice service did not complete that turn and has recovered. Please speak into the handheld microphone again.'
  }
  return clean
}

export default function VirtualHostApp() {
  const baseController = useOfficeVoiceController()
  const controller = useVisitLanguageAwareOfficeVoiceController(baseController)
  const proximity = useProximityGreeting(controller)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [lastUserText, setLastUserText] = useState('')
  const [lastAssistantText, setLastAssistantText] = useState('')
  const [vadUiState, setVadUiState] = useState<VadUiState>('idle')
  const lastPublishedAnswer = useRef('')
  const lastPublishedUser = useRef('')

  useEffect(() => {
    if (controller.actor !== 'operator') controller.setActor('operator')
  }, [controller.actor])

  useEffect(() => {
    const publishUser = (transcript: string) => {
      const visitId = visitLeaseRegistry.current()?.visitId ?? null
      const fingerprint = `${visitId ?? 'none'}|${transcript}`
      if (!transcript || fingerprint === lastPublishedUser.current) return
      lastPublishedUser.current = fingerprint
      publishSessionMessage({
        conversationId: controller.conversationId,
        visitId,
        role: 'user',
        text: transcript,
        source: 'final_user_utterance',
      })
    }
    const onSpeechStarted = () => setVadUiState('listening')
    const onSpeechStopped = () => setVadUiState('processing')
    const onUtterance = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null
      const transcript = String(detail?.transcript ?? detail?.text ?? '').trim()
      if (transcript) {
        setLastUserText(transcript)
        publishUser(transcript)
      }
      setVadUiState('processing')
    }
    const onDirectAssistant = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null
      const text = String(detail?.text ?? '').trim()
      if (text) setLastAssistantText(text)
    }
    const onSpeakingStopped = () => setVadUiState('idle')
    const onContinuousStopped = () => setVadUiState('idle')
    const onVisitBoundary = () => {
      lastPublishedUser.current = ''
      lastPublishedAnswer.current = ''
      setLastUserText('')
      setLastAssistantText('')
    }
    window.addEventListener('smartoffice:realtime-vad-speech-started', onSpeechStarted)
    window.addEventListener('smartoffice:realtime-vad-speech-stopped', onSpeechStopped)
    window.addEventListener('smartoffice:realtime-continuous-utterance', onUtterance)
    window.addEventListener('smartoffice:continuous-user-transcript', onUtterance)
    window.addEventListener('smartoffice:direct-assistant-caption', onDirectAssistant)
    window.addEventListener('smartoffice:realtime-speaking-stop', onSpeakingStopped)
    window.addEventListener('smartoffice:realtime-continuous-listening-stop', onContinuousStopped)
    window.addEventListener('smartoffice:visit-activated', onVisitBoundary)
    window.addEventListener('smartoffice:visit-revoked', onVisitBoundary)
    return () => {
      window.removeEventListener('smartoffice:realtime-vad-speech-started', onSpeechStarted)
      window.removeEventListener('smartoffice:realtime-vad-speech-stopped', onSpeechStopped)
      window.removeEventListener('smartoffice:realtime-continuous-utterance', onUtterance)
      window.removeEventListener('smartoffice:continuous-user-transcript', onUtterance)
      window.removeEventListener('smartoffice:direct-assistant-caption', onDirectAssistant)
      window.removeEventListener('smartoffice:realtime-speaking-stop', onSpeakingStopped)
      window.removeEventListener('smartoffice:realtime-continuous-listening-stop', onContinuousStopped)
      window.removeEventListener('smartoffice:visit-activated', onVisitBoundary)
      window.removeEventListener('smartoffice:visit-revoked', onVisitBoundary)
    }
  }, [controller.conversationId])

  const baseVisualState = controllerVisualState(
    controller.panel,
    controller.taskStatus,
    controller.active,
  )
  const visualState: VirtualHostVisualState = baseVisualState === 'idle'
    ? vadUiState === 'listening'
      ? 'listening'
      : vadUiState === 'processing'
        ? 'processing'
        : 'idle'
    : baseVisualState

  const isWaitingApproval = controller.taskStatus === 'waiting_approval'
  const isSendApproval = controller.pendingApprovalTool === 'outlook_send_approved_draft'
  const currentTranscript = controller.transcript.trim()
  const userCaption = currentTranscript || lastUserText
  const assistantCaption = controller.answer.trim() || lastAssistantText
  const voiceActive = controller.runtime.outputActive || controller.panel === 'speaking'
  const recipientName = useMemo(() => {
    const key = controller.pendingRecipientKey
    const entry = controller.office?.recipient_catalog?.find((item) => item.key === key)
    return entry?.name ?? controller.office?.recipient_name ?? key ?? 'Rico'
  }, [controller.office, controller.pendingRecipientKey])

  useEffect(() => {
    const transcript = controller.transcript.trim()
    if (!transcript) return
    setLastUserText(transcript)
    const visitId = visitLeaseRegistry.current()?.visitId ?? null
    const fingerprint = `${visitId ?? 'none'}|${transcript}`
    if (fingerprint === lastPublishedUser.current) return
    lastPublishedUser.current = fingerprint
    publishSessionMessage({
      conversationId: controller.conversationId,
      visitId,
      role: 'user',
      text: transcript,
      source: 'controller_final_transcript',
    })
  }, [controller.conversationId, controller.transcript])

  useEffect(() => {
    const answer = controller.answer.trim()
    if (!answer) return
    setLastAssistantText(answer)
    if (answer === lastPublishedAnswer.current) return
    lastPublishedAnswer.current = answer
    publishSessionMessage({
      conversationId: controller.conversationId,
      visitId: visitLeaseRegistry.current()?.visitId ?? null,
      role: 'assistant',
      text: answer,
      source: 'virtual_host_answer',
    })
  }, [controller.answer, controller.conversationId])

  useEffect(() => {
    if (!drawerOpen) return
    const closeTimer = window.setTimeout(() => setDrawerOpen(false), 30_000)
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDrawerOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.clearTimeout(closeTimer)
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
