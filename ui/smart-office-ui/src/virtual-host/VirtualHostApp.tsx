import { useEffect, useMemo, useState } from 'react'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import { useOfficeVoiceController } from '../voice/useOfficeVoiceController'
import ApprovalOverlay from './ApprovalOverlay'
import LiveCaption from './LiveCaption'
import OperatorDrawer from './OperatorDrawer'
import VirtualHostAvatar, { type VirtualHostVisualState } from './VirtualHostAvatar'
import './VirtualHost.css'
import './VirtualHostPhase3.css'
import './VirtualHostPhase4.css'

const ACTIVE_TASK_STATUSES = ['created', 'planning', 'running']

function stateText(
  visualState: VirtualHostVisualState,
  language: VoiceLanguage,
): string {
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

function visualStateFromController(
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
  if (active || (taskStatus !== null && ACTIVE_TASK_STATUSES.includes(taskStatus))) {
    return 'executing'
  }
  return 'idle'
}

function welcomeText(language: VoiceLanguage): string {
  return language === 'zh'
    ? '您好，我是您的 Smart Office 虚拟助手。请点击下方按钮告诉我需要处理的办公任务。'
    : 'Hello, I am your Smart Office virtual assistant. Select the button below and tell me how I can help.'
}

function micButtonText(
  visualState: VirtualHostVisualState,
  language: VoiceLanguage,
): string {
  const labels: Record<VirtualHostVisualState, { zh: string; en: string }> = {
    idle: { zh: '点击说话', en: 'Tap to speak' },
    connecting: { zh: '正在连接', en: 'Connecting' },
    listening: { zh: '结束说话', en: 'Finish speaking' },
    processing: { zh: '正在处理', en: 'Processing' },
    speaking: { zh: '停止朗读', en: 'Stop speaking' },
    executing: { zh: '任务执行中', en: 'Task in progress' },
    'waiting-approval': { zh: '请先确认操作', en: 'Confirmation required' },
    error: { zh: '重新尝试', en: 'Try again' },
  }
  return labels[visualState][language]
}

export default function VirtualHostApp() {
  const controller = useOfficeVoiceController()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [textComposerOpen, setTextComposerOpen] = useState(false)
  const [lastUserText, setLastUserText] = useState('')
  const [lastAssistantText, setLastAssistantText] = useState('')

  const visualState = visualStateFromController(
    controller.panel,
    controller.taskStatus,
    controller.active,
  )
  const isWaitingApproval = controller.taskStatus === 'waiting_approval'
  const isSendApproval = controller.pendingApprovalTool === 'outlook_send_approved_draft'
  const userCaption = controller.transcript.trim() || lastUserText
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
    if (answer) setLastAssistantText(answer)
  }, [controller.answer])

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

  async function handlePrimaryAction(): Promise<void> {
    if (visualState === 'listening') {
      await controller.endListening()
      return
    }
    if (visualState === 'speaking') {
      await controller.stopSpeaking()
      return
    }
    if (visualState === 'error') controller.clearError()
    setLastUserText('')
    setLastAssistantText('')
    await controller.beginListening()
  }

  async function handleTextSubmit(): Promise<void> {
    const text = controller.input.trim()
    if (!text) return
    setLastUserText(text)
    setLastAssistantText('')
    await controller.submit(text)
    controller.setInput('')
    setTextComposerOpen(false)
  }

  async function toggleFullscreen(): Promise<void> {
    if (document.fullscreenElement) {
      await document.exitFullscreen()
      return
    }
    await document.documentElement.requestFullscreen()
  }

  const primaryDisabled =
    visualState === 'connecting' ||
    visualState === 'processing' ||
    visualState === 'executing' ||
    visualState === 'waiting-approval'

  return (
    <main className={`virtual-host-shell state-${visualState}`}>
      <div className="virtual-host-background" aria-hidden="true">
        <span className="background-glow glow-one" />
        <span className="background-glow glow-two" />
        <span className="background-grid" />
      </div>

      <header className="virtual-host-header">
        <div className="virtual-host-brand">
          <span className="brand-symbol" aria-hidden="true">
            SO
          </span>
          <div>
            <strong>Smart Office</strong>
            <span>Virtual Host</span>
          </div>
        </div>
        <div className="virtual-host-header-actions">
          <span className={`system-ready ${controller.runtime.connected ? 'connected' : ''}`}>
            <i />
            {controller.runtime.connected
              ? controller.language === 'zh'
                ? '语音已连接'
                : 'Voice connected'
              : controller.language === 'zh'
                ? '系统就绪'
                : 'System ready'}
          </span>
          <button
            type="button"
            className="header-button language-button"
            onClick={() => controller.setLanguage(controller.language === 'zh' ? 'en' : 'zh')}
            aria-label="切换语言"
          >
            {controller.language === 'zh' ? '中文' : 'EN'}
          </button>
          <button
            type="button"
            className="header-button"
            onClick={() => void toggleFullscreen()}
            aria-label="切换全屏"
          >
            ⛶
          </button>
          <button
            type="button"
            className="header-button"
            onClick={() => setDrawerOpen(true)}
            aria-label="打开控制设置"
          >
            ⚙
          </button>
        </div>
      </header>

      <section className="virtual-host-stage">
        <div className="virtual-host-status" aria-live="polite">
          <span className={`status-dot status-${visualState}`} />
          <span>{stateText(visualState, controller.language)}</span>
        </div>

        <VirtualHostAvatar state={visualState} />

        <LiveCaption
          state={visualState}
          language={controller.language}
          userText={userCaption}
          assistantText={assistantCaption}
          welcomeText={welcomeText(controller.language)}
        />

        <div className="voice-dock">
          <button
            type="button"
            className={`primary-voice-button primary-${visualState}`}
            disabled={primaryDisabled}
            onClick={() => void handlePrimaryAction()}
          >
            <span className="primary-voice-icon" aria-hidden="true">
              {visualState === 'listening' ? '■' : visualState === 'speaking' ? 'Ⅱ' : '●'}
            </span>
            <span>{micButtonText(visualState, controller.language)}</span>
          </button>
          <button
            type="button"
            className="text-input-toggle"
            disabled={controller.busy || controller.listening}
            onClick={() => setTextComposerOpen((open) => !open)}
          >
            {controller.language === 'zh' ? '文字输入' : 'Type instead'}
          </button>
        </div>

        {textComposerOpen ? (
          <div className="text-composer">
            <input
              autoFocus
              value={controller.input}
              onChange={(event) => controller.setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void handleTextSubmit()
                if (event.key === 'Escape') setTextComposerOpen(false)
              }}
              placeholder={
                controller.language === 'zh'
                  ? '输入办公任务，例如：打开演示文稿并从第二页播放'
                  : 'Enter an office task'
              }
            />
            <button type="button" onClick={() => void handleTextSubmit()}>
              {controller.language === 'zh' ? '发送' : 'Send'}
            </button>
          </div>
        ) : null}
      </section>

      {controller.error ? (
        <button type="button" className="host-error-toast" onClick={controller.clearError}>
          <strong>{controller.language === 'zh' ? '操作未完成' : 'Action not completed'}</strong>
          <span>{controller.error}</span>
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
        <OperatorDrawer controller={controller} onClose={() => setDrawerOpen(false)} />
      ) : null}
    </main>
  )
}
