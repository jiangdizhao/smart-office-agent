import { useEffect, useState } from 'react'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'

type ApprovalOverlayProps = {
  language: VoiceLanguage
  recipientName: string
  isSendApproval: boolean
  voiceActive: boolean
  onApprove: () => Promise<void>
  onSkip: () => Promise<void>
  onCancelTask: () => Promise<void>
  onStopVoice: () => Promise<void>
}

type ApprovalAction = 'approve' | 'skip' | 'cancel' | 'stop-voice'

export default function ApprovalOverlay({
  language,
  recipientName,
  isSendApproval,
  voiceActive,
  onApprove,
  onSkip,
  onCancelTask,
  onStopVoice,
}: ApprovalOverlayProps) {
  const [busyAction, setBusyAction] = useState<ApprovalAction | null>(null)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busyAction) void runAction('skip', onSkip)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })

  async function runAction(action: ApprovalAction, operation: () => Promise<void>): Promise<void> {
    if (busyAction) return
    setBusyAction(action)
    try {
      await operation()
      await new Promise((resolve) => window.setTimeout(resolve, 550))
    } finally {
      setBusyAction(null)
    }
  }

  const zh = language === 'zh'
  const title = isSendApproval
    ? zh
      ? '是否现在发送 Outlook 邮件？'
      : 'Send the Outlook email now?'
    : zh
      ? '是否创建 Outlook 邮件草稿？'
      : 'Create the Outlook email draft?'
  const description = isSendApproval
    ? zh
      ? '邮件草稿已经准备好。确认后，系统会继续完成发送。'
      : 'The email draft is ready. Confirm to continue with sending.'
    : zh
      ? '邮件内容已经准备好。确认后，系统会在 Outlook 中创建并打开草稿。'
      : 'The email content is ready. Confirm to create and open the draft in Outlook.'

  return (
    <div className="approval-backdrop" role="presentation">
      <section
        className="approval-card exhibition-approval-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-title"
      >
        <span className="approval-kicker">{zh ? '需要您的确认' : 'Your confirmation is required'}</span>
        <h2 id="approval-title">{title}</h2>
        <p className="approval-description">{description}</p>

        <div className="approval-summary-row">
          <span>{zh ? '收件人' : 'Recipient'}</span>
          <strong>{recipientName}</strong>
        </div>

        <div className="approval-actions approval-main-actions">
          <button
            type="button"
            className="approval-primary"
            disabled={busyAction !== null}
            onClick={() => void runAction('approve', onApprove)}
          >
            {busyAction === 'approve'
              ? zh
                ? '正在提交…'
                : 'Submitting…'
              : isSendApproval
                ? zh
                  ? '确认发送'
                  : 'Send now'
                : zh
                  ? '创建草稿'
                  : 'Create draft'}
          </button>
          <button
            type="button"
            disabled={busyAction !== null}
            onClick={() => void runAction('skip', onSkip)}
          >
            {busyAction === 'skip' ? (zh ? '正在处理…' : 'Processing…') : zh ? '暂不执行' : 'Not now'}
          </button>
        </div>

        <div className="approval-secondary-actions">
          {voiceActive ? (
            <button
              type="button"
              disabled={busyAction !== null}
              onClick={() => void runAction('stop-voice', onStopVoice)}
            >
              {busyAction === 'stop-voice'
                ? zh
                  ? '正在停止…'
                  : 'Stopping…'
                : zh
                  ? '停止朗读'
                  : 'Stop speaking'}
            </button>
          ) : null}
          <button
            type="button"
            className="approval-cancel-task"
            disabled={busyAction !== null}
            onClick={() => void runAction('cancel', onCancelTask)}
          >
            {busyAction === 'cancel'
              ? zh
                ? '正在取消…'
                : 'Cancelling…'
              : zh
                ? '取消整个任务'
                : 'Cancel task'}
          </button>
        </div>

        <p className="approval-hint">
          {zh ? '按 Esc 可选择“暂不执行”。' : 'Press Esc to choose “Not now”.'}
        </p>
      </section>
    </div>
  )
}
