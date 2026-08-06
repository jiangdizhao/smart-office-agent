import { useState } from 'react'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
import { visitLanguagePreference } from '../voice/visitLanguagePreference'
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
  kind: InteractionWindowKind
  icon: string
  labelEn: string
  labelZh: string
  descriptionEn: string
  descriptionZh: string
}

const ACTIONS: RailAction[] = [
  {
    kind: 'contact',
    icon: '✎',
    labelEn: 'REGISTRATION',
    labelZh: '登记信息',
    descriptionEn: 'Leave your contact details',
    descriptionZh: '填写联系方式',
  },
  {
    kind: 'meeting',
    icon: '◇',
    labelEn: 'BOOK A MEETING',
    labelZh: '预约会议',
    descriptionEn: 'Choose a date and available staff',
    descriptionZh: '选择日期与可用员工',
  },
  {
    kind: 'recording',
    icon: '●',
    labelEn: 'LIVE RECORDING',
    labelZh: '实时录音',
    descriptionEn: 'Record an on-site conversation',
    descriptionZh: '录制现场对话',
  },
  {
    kind: 'transcript',
    icon: '≡',
    labelEn: 'SESSION SUMMARY',
    labelZh: '对话总结',
    descriptionEn: 'View this session’s key points',
    descriptionZh: '查看本次会话要点',
  },
  {
    kind: 'results',
    icon: '▣',
    labelEn: 'RESULT CENTER',
    labelZh: '结果中心',
    descriptionEn: 'Manage visitor records',
    descriptionZh: '管理访客档案',
  },
]

export default function InteractionActionRail() {
  const [notice, setNotice] = useState('')

  async function launch(kind: InteractionWindowKind): Promise<void> {
    const result = await openInteractionWindow({
      kind,
      conversationId: conversationId(),
      visitId: visitLeaseRegistry.current()?.visitId ?? null,
      language: visitLanguagePreference.current(),
    })
    setNotice(
      result.ok
        ? kind === 'results'
          ? 'ADMIN PASSWORD REQUIRED · 需要管理员密码'
          : 'OPENED BESIDE SARA · 已在 Sara 左侧打开'
        : 'UNABLE TO OPEN · 面板打开失败',
    )
    window.setTimeout(() => setNotice(''), 4_000)
  }

  return (
    <aside className="interaction-action-rail" aria-label="Touch services / 访客服务">
      <div className="interaction-action-heading">
        <span>TOUCH SERVICES</span>
        <strong>访客服务</strong>
      </div>
      <div className="interaction-action-list">
        {ACTIONS.map((action) => (
          <button
            key={action.kind}
            type="button"
            className="interaction-action-button"
            onClick={() => void launch(action.kind)}
            aria-label={`${action.labelEn} / ${action.labelZh}`}
          >
            <span className="interaction-action-icon" aria-hidden="true">
              {action.icon}
            </span>
            <span className="interaction-action-copy">
              <strong>{action.labelEn}</strong>
              <em>{action.labelZh}</em>
              <small>
                <span>{action.descriptionEn}</span>
                <span>{action.descriptionZh}</span>
              </small>
            </span>
          </button>
        ))}
      </div>
      {notice ? <span className="interaction-action-notice">{notice}</span> : null}
    </aside>
  )
}
