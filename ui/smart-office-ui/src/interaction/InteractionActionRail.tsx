import { useState } from 'react'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
import {
  openInteractionWindow,
  type InteractionWindowKind,
} from '../display/multiScreenWindowManager'
import './InteractionActionRail.css'

function conversationId(): string {
  const key = 'smartoffice_voice_conversation_id'
  const existing = sessionStorage.getItem(key)
  if (existing) return existing
  const created = `voice-${crypto.randomUUID()}`
  sessionStorage.setItem(key, created)
  return created
}

type RailAction = {
  kind: InteractionWindowKind | 'meeting'
  icon: string
  label: string
  description: string
  disabled?: boolean
}

const ACTIONS: RailAction[] = [
  { kind: 'contact', icon: '✎', label: '登记信息', description: '填写联系方式' },
  { kind: 'meeting', icon: '◇', label: '预约会议', description: '即将开放', disabled: true },
  { kind: 'recording', icon: '●', label: '实时录音', description: '录制现场对话' },
  { kind: 'transcript', icon: '≡', label: '对话记录', description: '仅当前 Session' },
  { kind: 'results', icon: '▣', label: '结果中心', description: '查看登记与录音' },
]

export default function InteractionActionRail() {
  const [notice, setNotice] = useState('')

  async function launch(kind: InteractionWindowKind): Promise<void> {
    const result = await openInteractionWindow({
      kind,
      conversationId: conversationId(),
      visitId: visitLeaseRegistry.current()?.visitId ?? null,
      language: 'zh',
    })
    setNotice(
      result.ok
        ? '已在左侧触摸屏打开'
        : result.blocked
          ? '请允许本站弹出窗口'
          : '窗口打开失败，请检查显示器设置',
    )
    window.setTimeout(() => setNotice(''), 4_000)
  }

  return (
    <aside className="interaction-action-rail" aria-label="访客互动功能">
      <div className="interaction-action-heading">
        <span>Touch Services</span>
        <strong>访客服务</strong>
      </div>
      <div className="interaction-action-list">
        {ACTIONS.map((action) => (
          <button
            key={action.kind}
            type="button"
            className="interaction-action-button"
            disabled={action.disabled}
            onClick={() => {
              if (action.kind !== 'meeting') void launch(action.kind)
            }}
          >
            <span className="interaction-action-icon" aria-hidden="true">
              {action.icon}
            </span>
            <span className="interaction-action-copy">
              <strong>{action.label}</strong>
              <small>{action.description}</small>
            </span>
          </button>
        ))}
      </div>
      {notice ? <span className="interaction-action-notice">{notice}</span> : null}
    </aside>
  )
}
