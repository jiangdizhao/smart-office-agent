import { useState } from 'react'
import type { VoiceLanguage } from './realtimeAgentRuntime'
import type { VoiceOutputProvider } from './voiceOutputManager'
import {
  useOfficeVoiceController,
  type OfficeActor,
  type OfficeAsrProvider,
  type OfficePanelState,
} from './useOfficeVoiceController'
import './VoiceDebugPanel.css'

const STATE_LABEL: Record<OfficePanelState, string> = {
  idle: '空闲',
  connecting: '正在连接',
  listening: '正在聆听',
  processing: '正在处理',
  speaking: '正在朗读',
  error: '发生错误',
}

export default function OfficeVoicePanel() {
  const controller = useOfficeVoiceController()
  const [expanded, setExpanded] = useState(true)
  const recipientCatalog = controller.office?.recipient_catalog ?? []
  const recipientCatalogText = recipientCatalog.length
    ? recipientCatalog.map((item) => `${item.name} [${item.key}]: ${item.email}`).join(' · ')
    : 'Rico [rico]: jiangdizhao@gmail.com'

  return (
    <aside className={`voice-debug-panel ${expanded ? 'expanded' : 'collapsed'}`}>
      <button className="voice-panel-toggle" onClick={() => setExpanded(!expanded)}>
        {expanded ? '收起 Phase 3 控制台' : '打开 Phase 3 控制台'}
      </button>
      {expanded ? (
        <div className="voice-panel-content">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-kicker">M3A-Fusion · Gate 3–5</span>
              <strong>演示、设备、摘要与 Outlook 邮件</strong>
            </div>
            <span className={`voice-state state-${controller.panel}`}>
              {STATE_LABEL[controller.panel]}
            </span>
          </div>

          <div className="voice-settings-grid">
            <label>
              语言
              <select
                value={controller.language}
                disabled={controller.listening || controller.busy}
                onChange={(event) =>
                  controller.setLanguage(event.target.value as VoiceLanguage)
                }
              >
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>
            </label>
            <label>
              身份
              <select
                value={controller.actor}
                disabled={controller.listening || controller.busy}
                onChange={(event) => controller.setActor(event.target.value as OfficeActor)}
              >
                <option value="visitor">Visitor</option>
                <option value="employee">Employee</option>
                <option value="operator">Operator</option>
              </select>
            </label>
            <label>
              语音识别
              <select
                value={controller.asr}
                disabled={controller.listening || controller.busy}
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
              语音输出
              <select
                value={controller.voice}
                disabled={controller.listening || controller.busy}
                onChange={(event) =>
                  void controller.setVoice(event.target.value as VoiceOutputProvider)
                }
              >
                <option value="realtime">GPT Realtime</option>
                <option value="none">仅文字</option>
              </select>
            </label>
          </div>

          <div className="voice-connection-row">
            <span>
              Voice WebRTC: {controller.runtime.connectionState} /{' '}
              {controller.runtime.dataChannelState}
            </span>
            <span>
              Mic: {controller.runtime.microphoneAttached ? 'attached' : 'released'}
            </span>
            <button
              disabled={
                controller.listening || controller.busy || controller.runtime.connected
              }
              onClick={() => void controller.connect()}
            >
              {controller.runtime.connected ? '已连接' : '连接语音'}
            </button>
          </div>

          <div className="voice-ptt-row">
            <button
              className={controller.listening ? 'voice-ptt listening' : 'voice-ptt'}
              disabled={controller.busy}
              onClick={() =>
                void (controller.listening
                  ? controller.endListening()
                  : controller.beginListening())
              }
            >
              {controller.listening ? '结束说话' : '点击说话'}
            </button>
            <button
              className="voice-stop"
              disabled={!controller.runtime.outputActive && controller.panel !== 'speaking'}
              onClick={() => void controller.stopSpeaking()}
            >
              停止朗读
            </button>
            <button
              disabled={controller.taskStatus !== 'waiting_approval'}
              onClick={() => void controller.approve('approve')}
            >
              {controller.pendingApprovalTool === 'outlook_send_approved_draft'
                ? '第二次批准并发送'
                : '第一次批准并创建草稿'}
            </button>
            <button
              disabled={controller.taskStatus !== 'waiting_approval'}
              onClick={() => void controller.approve('skip')}
            >
              跳过
            </button>
            <button
              className="voice-stop"
              disabled={!controller.active}
              onClick={() => void controller.approve('cancel')}
            >
              取消任务
            </button>
          </div>

          <div className="voice-text-test">
            <input
              value={controller.input}
              disabled={controller.listening || controller.busy}
              onChange={(event) => controller.setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !controller.busy) {
                  void controller.submit(controller.input)
                }
              }}
              placeholder="输入演示、设备、摘要或发给已配置联系人的 Outlook 邮件命令"
            />
            <button
              disabled={controller.listening || controller.busy}
              onClick={() => void controller.submit(controller.input)}
            >
              发送
            </button>
          </div>

          <div className="voice-result-grid">
            <div>
              <span>识别文本</span>
              <p>{controller.transcript || '—'}</p>
            </div>
            <div>
              <span>Agent 文本</span>
              <p>{controller.answer || '—'}</p>
            </div>
          </div>
          <div className="voice-result-grid">
            <div>
              <span>PowerPoint</span>
              <p>
                {controller.office?.slideshow_active
                  ? `Presenting ${controller.office.current_slide}/${controller.office.total_slides}`
                  : controller.office?.presentation_open
                    ? 'Ready'
                    : 'Closed'}
              </p>
            </div>
            <div>
              <span>设备</span>
              <p>
                Volume {controller.office?.volume_percent ?? '—'}% · Brightness{' '}
                {controller.office?.brightness_percent ?? '—'}%
              </p>
            </div>
          </div>
          <div className="voice-result-grid">
            <div>
              <span>Outlook 发件账号</span>
              <p>{controller.office?.sender_account_email ?? 'jiangdizhao1@outlook.com'}</p>
            </div>
            <div>
              <span>本次/默认收件人</span>
              <p>
                {controller.office?.recipient_name ?? 'Rico'} [{
                  controller.office?.recipient_key ??
                  controller.office?.default_recipient_key ??
                  'rico'
                }
                ]: {controller.office?.recipient_email ?? 'jiangdizhao@gmail.com'}
              </p>
            </div>
          </div>
          <div className="voice-result-grid">
            <div>
              <span>允许的邮件联系人</span>
              <p>{recipientCatalogText}</p>
            </div>
            <div>
              <span>发送边界</span>
              <p>仅白名单联系人 · 发送前第二次批准</p>
            </div>
          </div>

          <div className="voice-ptt-row">
            {controller.contentUrl ? (
              <button onClick={controller.openArtifact}>打开摘要</button>
            ) : null}
          </div>
          <div className="voice-diagnostics">
            <span>Actor: {controller.actor}</span>
            <span>Route: {controller.route || '—'}</span>
            <span>Permission: {controller.permission || '—'}</span>
            <span>Tool: {controller.tool || '—'}</span>
            <span>
              Verification:{' '}
              {controller.verified === null
                ? '—'
                : controller.verified
                  ? 'PASS'
                  : 'FAIL'}
            </span>
            <span>Task: {controller.taskId || '—'}</span>
            <span>Task status: {controller.taskStatus || '—'}</span>
            <span>Email send: allowlist + second approval</span>
          </div>
          {controller.error ? (
            <div className="voice-error" onClick={controller.clearError}>
              {controller.error}
            </div>
          ) : null}
          <p className="voice-safety-note">
            本机 Classic Outlook 发件账号固定为 jiangdizhao1@outlook.com。收件人从 Backend
            白名单中按联系人键名选择；创建草稿和发送邮件分别需要两次独立批准。第二次批准后会先删除“仅保存为草稿、尚未发送”的提示，再调用
            Outlook 发送。模型不能直接提供任意邮箱地址。
          </p>
        </div>
      ) : null}
    </aside>
  )
}
