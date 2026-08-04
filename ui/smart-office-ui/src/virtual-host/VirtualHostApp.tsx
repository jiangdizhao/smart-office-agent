import { useEffect, useMemo, useRef, useState } from 'react'
import { publishSessionMessage } from '../interaction/sessionEventBus'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
import { useProximityGreeting } from '../vision/useProximityGreeting'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import {
  useOfficeVoiceController,
  type ConversationPhase,
} from '../voice/useOfficeVoiceController'
import ApprovalOverlay from './ApprovalOverlay'
import LiveCaption from './LiveCaption'
import OperatorDrawer from './OperatorDrawer'
import VirtualHostAvatar, { type VirtualHostVisualState } from './VirtualHostAvatar'
import './VirtualHost.css'
import './VirtualHostPhase3.css'
import './VirtualHostPhase4.css'
import './ConversationRecording.css'
import './ContinuousVoice.css'

const ACTIVE_TASK_STATUSES = ['created', 'planning', 'running']
type VadUiState = 'idle' | 'listening' | 'processing'

function stateText(
  visualState: VirtualHostVisualState,
  conversationPhase: ConversationPhase,
  language: VoiceLanguage,
): string {
  if (visualState === 'idle') {
    const idleLabels: Record<ConversationPhase, { zh: string; en: string }> = {
      standby: { zh: '随时为您服务', en: 'Ready to help' },
      engaged: { zh: '对话进行中', en: 'Conversation in progress' },
      awaiting_user: { zh: '等待您继续', en: 'Waiting for you' },
      task_active: { zh: '正在处理本次对话中的任务', en: 'Working on this conversation' },
      closing: { zh: '本次服务即将结束', en: 'Closing this conversation' },
    }
    return idleLabels[conversationPhase][language]
  }
  const labels: Record<VirtualHostVisualState, { zh: string; en: string }> = {
    idle: { zh: '随时为您服务', en: 'Ready to help' },
    connecting: { zh: '正在连接语音服务', en: 'Connecting voice service' },
    listening: { zh: '正在聆听', en: 'Listening' },
    processing: { zh: '正在理解您的请求', en: 'Understanding your request' },
    speaking: { zh: '正在为您说明', en: 'Speaking' },
    executing: { zh: '正在执行办公任务', en: 'Working on your office task' },
    'waiting-approval': { zh: '等待您的确认', en: 'Waiting for confirmation' },
    error: { zh: '需要重新尝试', en: 'Please try again' },
  }
  return labels[visualState][language]
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

function welcomeText(language: VoiceLanguage): string {
  return language === 'zh'
    ? '您好，我是 Sara，Smart Office 数字管理员与企业解决方案顾问。访客靠近后，手持麦克风会自动进入对话状态。'
    : 'Hello, I am Sara, your Smart Office Digital Manager and Enterprise Solution Consultant. The handheld microphone activates when a visitor approaches.'
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
  const controller = useOfficeVoiceController()
  const proximity = useProximityGreeting(controller)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [lastUserText, setLastUserText] = useState('')
  const [lastAssistantText, setLastAssistantText] = useState('')
  const [vadUiState, setVadUiState] = useState<VadUiState>('idle')
  const lastPublishedAnswer = useRef('')

  useEffect(() => {
    if (controller.actor !== 'operator') controller.setActor('operator')
  }, [controller.actor])

  useEffect(() => {
    const onSpeechStarted = () => setVadUiState('listening')
    const onSpeechStopped = () => setVadUiState('processing')
    const onUtterance = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null
      const transcript = String(detail?.transcript ?? '').trim()
      if (transcript) setLastUserText(transcript)
      setVadUiState('processing')
    }
    const onDirectAssistant = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null
      const text = String(detail?.text ?? '').trim()
      if (text) setLastAssistantText(text)
    }
    const onSpeakingStopped = () => setVadUiState('idle')
    const onContinuousStopped = () => setVadUiState('idle')
    window.addEventListener('smartoffice:realtime-vad-speech-started', onSpeechStarted)
    window.addEventListener('smartoffice:realtime-vad-speech-stopped', onSpeechStopped)
    window.addEventListener('smartoffice:realtime-continuous-utterance', onUtterance)
    window.addEventListener('smartoffice:continuous-user-transcript', onUtterance)
    window.addEventListener('smartoffice:direct-assistant-caption', onDirectAssistant)
    window.addEventListener('smartoffice:realtime-speaking-stop', onSpeakingStopped)
    window.addEventListener('smartoffice:realtime-continuous-listening-stop', onContinuousStopped)
    return () => {
      window.removeEventListener('smartoffice:realtime-vad-speech-started', onSpeechStarted)
      window.removeEventListener('smartoffice:realtime-vad-speech-stopped', onSpeechStopped)
      window.removeEventListener('smartoffice:realtime-continuous-utterance', onUtterance)
      window.removeEventListener('smartoffice:continuous-user-transcript', onUtterance)
      window.removeEventListener('smartoffice:direct-assistant-caption', onDirectAssistant)
      window.removeEventListener('smartoffice:realtime-speaking-stop', onSpeakingStopped)
      window.removeEventListener('smartoffice:realtime-continuous-listening-stop', onContinuousStopped)
    }
  }, [])

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
    if (transcript) setLastUserText(transcript)
  }, [controller.transcript])

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

  const continuousStatus = controller.runtime.continuousListening
    ? visualState === 'listening'
      ? controller.language === 'zh' ? '正在听取手持麦克风' : 'Listening to the handheld microphone'
      : visualState === 'processing'
        ? controller.language === 'zh' ? '正在理解您的问题' : 'Understanding your question'
        : visualState === 'speaking'
          ? controller.language === 'zh' ? '您可以直接说话打断 Sara' : 'Speak to interrupt Sara at any time'
          : controller.language === 'zh' ? '手持麦克风已就绪，请直接说话' : 'Handheld microphone ready — just speak'
    : controller.language === 'zh'
      ? '访客靠近后自动开启手持麦克风'
      : 'The handheld microphone activates when a visitor approaches'

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
          <div><strong>Smart Office</strong><span>{controller.language === 'zh' ? '数字管理员 · 解决方案顾问' : 'Digital Manager · Solution Consultant'}</span></div>
        </div>
        <div className="virtual-host-header-actions">
          <span className={`system-ready ${controller.runtime.connected ? 'connected' : ''}`}>
            <i />
            {controller.runtime.connected
              ? controller.language === 'zh' ? '语音已连接' : 'Voice connected'
              : controller.language === 'zh' ? '系统就绪' : 'System ready'}
          </span>
          <button type="button" className="header-button language-button" onClick={() => controller.setLanguage(controller.language === 'zh' ? 'en' : 'zh')} aria-label="切换语言">
            {controller.language === 'zh' ? '中文' : 'EN'}
          </button>
          <button type="button" className="header-button" onClick={() => void toggleFullscreen()} aria-label="切换全屏">⛶</button>
          <button type="button" className="header-button" onClick={() => setDrawerOpen(true)} aria-label="打开控制设置">⚙</button>
        </div>
      </header>

      <section className="virtual-host-stage">
        <div className="virtual-host-status" aria-live="polite">
          <span className={`status-dot status-${visualState}`} />
          <span>{stateText(visualState, controller.conversationPhase, controller.language)}</span>
        </div>

        <VirtualHostAvatar state={visualState} />

        <LiveCaption
          state={visualState}
          language={controller.language}
          userText={userCaption}
          assistantText={assistantCaption}
          welcomeText={welcomeText(controller.language)}
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
