import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ConversationAudioRecorder } from '../recording/conversationAudioRecorder'
import './InteractionApp.css'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

type InteractionKind = 'contact' | 'recording' | 'transcript' | 'unknown'
type ConversationMessage = {
  role?: 'user' | 'assistant' | 'system'
  text?: string
  timestamp?: string
}
type ConversationEnvelope = {
  state?: {
    visit_id?: string | null
    conversation_phase?: string
    recent_messages?: ConversationMessage[]
  }
}

function kindFromPath(): InteractionKind {
  const match = location.pathname.match(/\/interaction\/(contact|recording|transcript)\/?$/)
  return (match?.[1] as InteractionKind | undefined) ?? 'unknown'
}
function query(name: string): string {
  return new URLSearchParams(location.search).get(name)?.trim() ?? ''
}
function closeWindow(): void { window.close() }
function formatDuration(total: number): string {
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}
function formatTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function Shell({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <main className="interaction-shell">
      <header className="interaction-header">
        <div><span>Smart Office · Touch Display</span><h1>{title}</h1><p>{subtitle}</p></div>
        <button type="button" className="interaction-close" onClick={closeWindow}>关闭</button>
      </header>
      <section className="interaction-content">{children}</section>
    </main>
  )
}

function ContactRegistration({ conversationId, visitId }: { conversationId: string; visitId: string }) {
  const [name, setName] = useState('')
  const [company, setCompany] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [interest, setInterest] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [consent, setConsent] = useState(false)
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState('')
  const [error, setError] = useState('')
  const options = ['PowerPoint 语音控制', 'Outlook 助手', '智能接待', '会议与录音', '其他']

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (saving) return
    setError('')
    if (!name.trim()) return setError('请填写姓名。')
    if (!email.trim() && !phone.trim()) return setError('请至少填写电子邮箱或电话号码之一。')
    if (!consent) return setError('需要确认同意展会后联系，才能保存登记信息。')
    setSaving(true)
    try {
      const response = await fetch(`${API_BASE_URL}/api/contact-records`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          conversation_id: conversationId,
          visit_id: visitId || null,
          name: name.trim(), company: company.trim() || null,
          email: email.trim() || null, phone: phone.trim() || null,
          interest_tags: interest, notes: notes.trim() || null,
          contact_consent: true,
          consent_statement_version: 'expo-contact-v1',
          source: 'left_touch_display',
        }),
      })
      if (!response.ok) throw new Error((await response.text()) || `登记失败：${response.status}`)
      const payload = (await response.json()) as { contact_id?: string }
      setSuccess(payload.contact_id ?? 'saved')
      window.setTimeout(closeWindow, 1800)
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally { setSaving(false) }
  }

  if (success) return (
    <Shell title="登记成功" subtitle="感谢您留下联系方式，本窗口即将自动关闭。">
      <div className="success-card"><b>✓</b><strong>登记已完成</strong><p>工作人员可在展会后按照您授权的方式与您联系。</p></div>
    </Shell>
  )

  return (
    <Shell title="登记信息" subtitle="请在触摸屏上填写。完成后，本窗口会自动关闭并恢复网站展示。">
      <form className="contact-form" onSubmit={(event) => void submit(event)}>
        <div className="form-grid">
          <label><span>姓名 *</span><input autoFocus maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label><span>公司 / 机构</span><input maxLength={160} value={company} onChange={(e) => setCompany(e.target.value)} /></label>
          <label><span>电子邮箱</span><input type="email" maxLength={240} value={email} onChange={(e) => setEmail(e.target.value)} /></label>
          <label><span>电话号码</span><input type="tel" maxLength={80} value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
        </div>
        <fieldset><legend>感兴趣的功能</legend><div className="interest-options">
          {options.map((option) => <label key={option} className={interest.includes(option) ? 'selected' : ''}>
            <input type="checkbox" checked={interest.includes(option)} onChange={() => setInterest((current) => current.includes(option) ? current.filter((item) => item !== option) : [...current, option])} />
            <span>{option}</span>
          </label>)}
        </div></fieldset>
        <label><span>备注</span><textarea rows={3} maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        <label className="consent-field"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /><span>我同意 Smart Office 团队保存以上联系方式，并在展会后就相关产品或演示与我联系。本授权不包含人脸或其他生物识别信息。</span></label>
        {error ? <div className="interaction-error" role="alert">{error}</div> : null}
        <div className="form-actions"><button type="button" className="secondary" onClick={closeWindow}>取消</button><button type="submit" className="primary" disabled={saving}>{saving ? '正在保存…' : '完成登记'}</button></div>
      </form>
    </Shell>
  )
}

function LiveRecording({ conversationId }: { conversationId: string }) {
  const recorder = useRef(new ConversationAudioRecorder())
  const [recording, setRecording] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [duration, setDuration] = useState(0)
  const [startedAt, setStartedAt] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!recording || !startedAt) return
    const update = () => setDuration(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)))
    update(); const timer = window.setInterval(update, 500)
    return () => window.clearInterval(timer)
  }, [recording, startedAt])
  useEffect(() => () => { void recorder.current.dispose(false) }, [])

  async function start(): Promise<void> {
    setError(''); setSaved(false)
    try { await recorder.current.start(); setStartedAt(Date.now()); setDuration(0); setRecording(true) }
    catch (value) { setError(value instanceof Error ? value.message : String(value)) }
  }
  async function stop(): Promise<void> {
    if (!recording || saving) return
    setRecording(false); setSaving(true); setError('')
    try {
      const result = await recorder.current.stop()
      if (!result?.blob.size) throw new Error('录音为空，未生成文件。')
      setDuration(result.durationSeconds)
      const extension = result.mimeType.includes('mp4') ? 'm4a' : 'webm'
      const form = new FormData()
      form.append('file', result.blob, `human-conversation-${new Date(result.startedAt).toISOString().replace(/[:.]/g, '-')}.${extension}`)
      form.append('language', 'zh')
      const response = await fetch(`${API_BASE_URL}/api/human-recordings/${encodeURIComponent(conversationId)}`, { method: 'POST', body: form })
      if (!response.ok) throw new Error((await response.text()) || `录音上传失败：${response.status}`)
      setSaved(true); window.setTimeout(closeWindow, 4000)
    } catch (value) { setError(value instanceof Error ? value.message : String(value)) }
    finally { setSaving(false) }
  }

  return (
    <Shell title="实时录音" subtitle="录制现场人员之间的对话。停止并保存后，本窗口会自动关闭。">
      <div className="recording-workspace">
        <div className={`recording-orb ${recording ? 'active' : ''}`}><span /></div>
        <strong className="recording-time">{formatDuration(duration)}</strong>
        <p>{saving ? '正在生成并上传录音文件…' : saved ? '录音已保存。本窗口将在数秒后自动关闭。' : recording ? '正在录音。请在谈话结束后点击“停止并保存”。' : '点击开始后，浏览器会请求使用麦克风。'}</p>
        {error ? <div className="interaction-error">{error}</div> : null}
        <div className="form-actions">
          {recording ? <button className="danger" onClick={() => void stop()}>停止并保存</button> : <button className="primary" disabled={saving || saved} onClick={() => void start()}>{saving ? '正在保存…' : saved ? '已保存' : '开始录音'}</button>}
          {!recording && !saving && !saved ? <button className="secondary" onClick={closeWindow}>取消</button> : null}
        </div>
      </div>
    </Shell>
  )
}

function SessionTranscript({ conversationId, expectedVisitId }: { conversationId: string; expectedVisitId: string }) {
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [phase, setPhase] = useState('')
  const [error, setError] = useState('')
  const [ended, setEnded] = useState(false)
  useEffect(() => {
    let disposed = false
    const refresh = async () => {
      try {
        const params = new URLSearchParams({ language: 'zh', actor_type: 'visitor' })
        const response = await fetch(`${API_BASE_URL}/api/conversations/${encodeURIComponent(conversationId)}?${params}`)
        if (!response.ok) throw new Error(`读取对话记录失败：${response.status}`)
        const payload = (await response.json()) as ConversationEnvelope
        if (disposed) return
        const visit = payload.state?.visit_id ?? ''
        if (expectedVisitId && visit && visit !== expectedVisitId) {
          setMessages([]); setEnded(true); window.setTimeout(closeWindow, 2000); return
        }
        setMessages(payload.state?.recent_messages ?? [])
        setPhase(payload.state?.conversation_phase ?? '')
        setError('')
      } catch (value) { if (!disposed) setError(value instanceof Error ? value.message : String(value)) }
    }
    void refresh(); const timer = window.setInterval(() => void refresh(), 1000); const close = window.setTimeout(closeWindow, 120000)
    return () => { disposed = true; clearInterval(timer); clearTimeout(close) }
  }, [conversationId, expectedVisitId])

  return (
    <Shell title="当前对话记录" subtitle="仅显示当前访客 Session；新访客到来后，本窗口会自动关闭。">
      <div className="transcript-toolbar"><span>状态：{phase || '正在连接'}</span><span>{messages.length} 条消息</span></div>
      {ended ? <div className="success-card compact"><strong>当前 Session 已结束</strong><p>为避免向下一位访客显示旧内容，本窗口即将关闭。</p></div>
        : error ? <div className="interaction-error">{error}</div>
          : messages.length ? <div className="transcript-list">{messages.map((message, index) => <article key={`${message.timestamp ?? 'm'}-${index}`} className={`transcript-message role-${message.role ?? 'system'}`}><header><strong>{message.role === 'user' ? '访客' : message.role === 'assistant' ? 'Sara' : '系统'}</strong><time>{formatTime(message.timestamp)}</time></header><p>{message.text}</p></article>)}</div>
            : <div className="transcript-empty">当前 Session 还没有可显示的对话内容。</div>}
    </Shell>
  )
}

export default function InteractionApp() {
  const kind = useMemo(kindFromPath, [])
  const conversationId = query('conversation_id')
  const visitId = query('visit_id')
  if (!conversationId) return <Shell title="无法打开" subtitle="缺少当前会话标识。"><div className="interaction-error">请从中间屏幕上的 Agent 重新打开。</div></Shell>
  if (kind === 'contact') return <ContactRegistration conversationId={conversationId} visitId={visitId} />
  if (kind === 'recording') return <LiveRecording conversationId={conversationId} />
  if (kind === 'transcript') return <SessionTranscript conversationId={conversationId} expectedVisitId={visitId} />
  return <Shell title="未知功能" subtitle="当前触摸页面不存在。"><button className="primary" onClick={closeWindow}>关闭</button></Shell>
}
