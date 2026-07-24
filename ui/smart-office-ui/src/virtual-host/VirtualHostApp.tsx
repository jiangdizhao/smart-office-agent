import { useEffect, useMemo, useState } from 'react'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import type { VoiceOutputProvider } from '../voice/voiceOutputManager'
import {
  useOfficeVoiceController,
  type OfficeActor,
  type OfficeAsrProvider,
} from '../voice/useOfficeVoiceController'
import LiveCaption from './LiveCaption'
import VirtualHostAvatar, { type VirtualHostVisualState } from './VirtualHostAvatar'
import './VirtualHost.css'
import './VirtualHostPhase3.css'

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
        <div className="approval-backdrop" role="presentation">
          <section className="approval-card" role="dialog" aria-modal="true">
            <span className="approval-kicker">
              {controller.language === 'zh' ? '需要确认' : 'Confirmation required'}
            </span>
            <h2>
              {isSendApproval
                ? controller.language === 'zh'
                  ? '是否现在发送 Outlook 邮件？'
                  : 'Send the Outlook email now?'
                : controller.language === 'zh'
                  ? '是否创建 Outlook 邮件草稿？'
                  : 'Create the Outlook email draft?'}
            </h2>
            <p>
              {controller.language === 'zh' ? '收件人' : 'Recipient'}：
              <strong>{recipientName}</strong>
            </p>
            <div className="approval-actions">
              <button
                type="button"
                className="approval-primary"
                onClick={() => void controller.approve('approve')}
              >
                {isSendApproval
                  ? controller.language === 'zh'
                    ? '确认发送'
                    : 'Send now'
                  : controller.language === 'zh'
                    ? '创建草稿'
                    : 'Create draft'}
              </button>
              <button type="button" onClick={() => void controller.approve('skip')}>
                {controller.language === 'zh' ? '暂不执行' : 'Not now'}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {drawerOpen ? (
        <div className="operator-drawer-layer">
          <button
            type="button"
            className="drawer-backdrop"
            aria-label="关闭控制设置"
            onClick={() => setDrawerOpen(false)}
          />
          <aside className="operator-drawer" aria-label="控制设置">
            <div className="drawer-heading">
              <div>
                <span>{controller.language === 'zh' ? '操作员设置' : 'Operator settings'}</span>
                <strong>
                  {controller.language === 'zh' ? '控制与诊断入口' : 'Controls and diagnostics'}
                </strong>
              </div>
              <button type="button" onClick={() => setDrawerOpen(false)} aria-label="关闭">
                ×
              </button>
            </div>

            <div className="drawer-settings">
              <label>
                <span>{controller.language === 'zh' ? '界面语言' : 'Language'}</span>
                <select
                  value={controller.language}
                  onChange={(event) => controller.setLanguage(event.target.value as VoiceLanguage)}
                >
                  <option value="zh">中文</option>
                  <option value="en">English</option>
                </select>
              </label>
              <label>
                <span>{controller.language === 'zh' ? '用户身份' : 'Actor'}</span>
                <select
                  value={controller.actor}
                  onChange={(event) => controller.setActor(event.target.value as OfficeActor)}
                >
                  <option value="visitor">Visitor</option>
                  <option value="employee">Employee</option>
                  <option value="operator">Operator</option>
                </select>
              </label>
              <label>
                <span>{controller.language === 'zh' ? '语音识别' : 'Speech recognition'}</span>
                <select
                  value={controller.asr}
                  onChange={(event) =>
                    controller.setAsr(event.target.value as OfficeAsrProvider)
                  }
                >
                  <option value="realtime">GPT Realtime</option>
                  <option value="browser" disabled={!controller.browserAsrAvailable}>
                    Browser ASR
                  </option>
                </select>
              </label>
              <label>
                <span>{controller.language === 'zh' ? '语音输出' : 'Voice output'}</span>
                <select
                  value={controller.voice}
                  onChange={(event) =>
                    void controller.setVoice(event.target.value as VoiceOutputProvider)
                  }
                >
                  <option value="realtime">GPT Realtime</option>
                  <option value="none">
                    {controller.language === 'zh' ? '仅文字' : 'Text only'}
                  </option>
                </select>
              </label>
            </div>

            <div className="drawer-status-card">
              <div>
                <span>WebRTC</span>
                <strong>{controller.runtime.connectionState}</strong>
              </div>
              <div>
                <span>{controller.language === 'zh' ? '麦克风' : 'Microphone'}</span>
                <strong>
                  {controller.runtime.microphoneAttached
                    ? controller.language === 'zh'
                      ? '正在使用'
                      : 'Attached'
                    : controller.language === 'zh'
                      ? '已释放'
                      : 'Released'}
                </strong>
              </div>
            </div>

            <button
              type="button"
              className="drawer-primary-action"
              disabled={controller.runtime.connected || controller.busy || controller.listening}
              onClick={() => void controller.connect()}
            >
              {controller.runtime.connected
                ? controller.language === 'zh'
                  ? '语音已连接'
                  : 'Voice connected'
                : controller.language === 'zh'
                  ? '连接语音服务'
                  : 'Connect voice service'}
            </button>

            {controller.active ? (
              <button
                type="button"
                className="drawer-danger-action"
                onClick={() => void controller.approve('cancel')}
              >
                {controller.language === 'zh' ? '取消当前任务' : 'Cancel current task'}
              </button>
            ) : null}

            <button
              type="button"
              className="drawer-debug-link"
              onClick={() => window.location.assign('/debug')}
            >
              {controller.language === 'zh' ? '打开完整调试控制台' : 'Open full debug console'}
              <span aria-hidden="true">↗</span>
            </button>
          </aside>
        </div>
      ) : null}
    </main>
  )
}
