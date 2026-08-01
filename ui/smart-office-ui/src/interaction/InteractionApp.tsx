import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ConversationAudioRecorder } from '../recording/conversationAudioRecorder'
import {
  subscribeSessionMessages,
  type SessionMessageEvent,
} from './sessionEventBus'
import './InteractionApp.css'
import './ResultCenter.css'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

type InteractionKind = 'contact' | 'recording' | 'transcript' | 'results' | 'unknown'
type ConversationMessage = {
  role?: 'user' | 'assistant' | 'system'
  text?: string
  timestamp?: string
  source?: string
}
type ConversationEnvelope = {
  state?: {
    visit_id?: string | null
    conversation_phase?: string
    recent_messages?: ConversationMessage[]
  }
}
type ContactRecord = {
  contact_id: string
  created_at: string
  name: string
  company?: string | null
  email?: string | null
  phone?: string | null
  interest_tags?: string[]
  notes?: string | null
  contact_consent?: boolean
  visit_id?: string | null
}
type RecordingRecord = {
  filename: string
  audio_path: string
  artifact_url: string
  conversation_id: string
  size_bytes: number
  uploaded_at: string
}

function kindFromPath(): InteractionKind {
  const match = location.pathname.match(/\/interaction\/(contact|recording|transcript|results)\/?$/)
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
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
function formatDate(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}
function resultCenterLocation(conversationId: string, visitId: string): string {
  const url = new URL('/interaction/results', location.origin)
  url.searchParams.set('conversation_id', conversationId)
  if (visitId) url.searchParams.set('visit_id', visitId)
  return url.toString()
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
  const closeTimer = useRef<number | null>(null)
  const options = ['PowerPoint 语音控制', 'Outlook 助手', '智能接待', '会议与录音', '其他']

  useEffect(() => () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
  }, [])

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
      closeTimer.current = window.setTimeout(closeWindow, 5000)
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally { setSaving(false) }
  }

  if (success) return (
    <Shell title="登记成功" subtitle="信息已写入联系人数据库，本窗口将在数秒后自动关闭。">
      <div className="success-card">
        <b>✓</b><strong>登记已完成</strong>
        <p>记录编号：{success}</p>
        <p>工作人员可以从中间屏幕的“结果中心”查看和导出全部登记信息。</p>
        <div className="saved-result-actions">
          <button type="button" onClick={() => {
            if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
            location.assign(resultCenterLocation(conversationId, visitId))
          }}>立即查看结果中心</button>
        </div>
      </div>
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

function LiveRecording({ conversationId, visitId }: { conversationId: string; visitId: string }) {
  const recorder = useRef(new ConversationAudioRecorder())
  const closeTimer = useRef<number | null>(null)
  const [recording, setRecording] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<{ filename: string; path: string; url: string } | null>(null)
  const [duration, setDuration] = useState(0)
  const [startedAt, setStartedAt] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!recording || !startedAt) return
    const update = () => setDuration(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)))
    update(); const timer = window.setInterval(update, 500)
    return () => window.clearInterval(timer)
  }, [recording, startedAt])
  useEffect(() => () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    void recorder.current.dispose(false)
  }, [])

  async function start(): Promise<void> {
    setError(''); setSaved(null)
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
      const payload = (await response.json()) as { audio_filename?: string; audio_path?: string; artifact_url?: string }
      setSaved({
        filename: payload.audio_filename ?? 'recording',
        path: payload.audio_path ?? '',
        url: payload.artifact_url ?? '',
      })
      closeTimer.current = window.setTimeout(closeWindow, 8000)
    } catch (value) { setError(value instanceof Error ? value.message : String(value)) }
    finally { setSaving(false) }
  }

  return (
    <Shell title="实时录音" subtitle="录制现场人员之间的对话。停止并保存后，本窗口会自动关闭。">
      <div className="recording-workspace">
        <div className={`recording-orb ${recording ? 'active' : ''}`}><span /></div>
        <strong className="recording-time">{formatDuration(duration)}</strong>
        <p>{saving ? '正在生成并上传录音文件…' : saved ? '录音已保存。本窗口将在数秒后自动关闭。' : recording ? '正在录音。请在谈话结束后点击“停止并保存”。' : '点击开始后，浏览器会请求使用麦克风。'}</p>
        {saved ? <div className="saved-result-details"><strong>{saved.filename}</strong><span>{saved.path}</span><span>可从“结果中心”播放或下载。</span></div> : null}
        {error ? <div className="interaction-error">{error}</div> : null}
        <div className="form-actions">
          {recording ? <button className="danger" onClick={() => void stop()}>停止并保存</button> : <button className="primary" disabled={saving || Boolean(saved)} onClick={() => void start()}>{saving ? '正在保存…' : saved ? '已保存' : '开始录音'}</button>}
          {!recording && !saving && !saved ? <button className="secondary" onClick={closeWindow}>取消</button> : null}
        </div>
        {saved ? <div className="saved-result-actions">
          {saved.url ? <a href={`${API_BASE_URL}${saved.url}`} download>下载录音</a> : null}
          <button type="button" onClick={() => {
            if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
            location.assign(resultCenterLocation(conversationId, visitId))
          }}>查看结果中心</button>
        </div> : null}
      </div>
    </Shell>
  )
}

function messageKey(message: ConversationMessage): string {
  return `${message.role ?? 'system'}|${String(message.text ?? '').trim()}`
}

function SessionTranscriptBody({
  conversationId,
  expectedVisitId,
  autoClose,
}: {
  conversationId: string
  expectedVisitId: string
  autoClose: boolean
}) {
  const [backendMessages, setBackendMessages] = useState<ConversationMessage[]>([])
  const [liveMessages, setLiveMessages] = useState<ConversationMessage[]>([])
  const [phase, setPhase] = useState('')
  const [error, setError] = useState('')
  const [ended, setEnded] = useState(false)

  useEffect(() => subscribeSessionMessages((event: SessionMessageEvent) => {
    if (event.conversationId !== conversationId) return
    if (expectedVisitId && event.visitId && event.visitId !== expectedVisitId) return
    setLiveMessages((current) => [
      ...current,
      {
        role: event.role,
        text: event.text,
        timestamp: event.timestamp,
        source: event.source,
      },
    ].slice(-64))
    setPhase(event.role === 'user' ? 'engaged' : 'awaiting_user')
  }), [conversationId, expectedVisitId])

  useEffect(() => {
    let disposed = false
    const refresh = async () => {
      try {
        const params = new URLSearchParams({ language: 'zh', actor_type: 'operator' })
        const response = await fetch(`${API_BASE_URL}/api/conversations/${encodeURIComponent(conversationId)}?${params}`)
        if (!response.ok) throw new Error(`读取对话记录失败：${response.status}`)
        const payload = (await response.json()) as ConversationEnvelope
        if (disposed) return
        const visit = payload.state?.visit_id ?? ''
        if (expectedVisitId && visit && visit !== expectedVisitId) {
          setEnded(true)
          if (autoClose) window.setTimeout(closeWindow, 2000)
          return
        }
        setBackendMessages(payload.state?.recent_messages ?? [])
        setPhase(payload.state?.conversation_phase ?? '')
        setError('')
      } catch (value) { if (!disposed) setError(value instanceof Error ? value.message : String(value)) }
    }
    void refresh(); const timer = window.setInterval(() => void refresh(), 1000)
    const close = autoClose ? window.setTimeout(closeWindow, 120000) : null
    return () => {
      disposed = true
      clearInterval(timer)
      if (close !== null) clearTimeout(close)
    }
  }, [autoClose, conversationId, expectedVisitId])

  const messages = useMemo(() => {
    const merged = new Map<string, ConversationMessage>()
    for (const message of [...backendMessages, ...liveMessages]) {
      const text = String(message.text ?? '').trim()
      if (!text) continue
      merged.set(messageKey({ ...message, text }), { ...message, text })
    }
    return [...merged.values()].sort((left, right) =>
      String(left.timestamp ?? '').localeCompare(String(right.timestamp ?? '')),
    )
  }, [backendMessages, liveMessages])

  return (
    <>
      <div className="transcript-toolbar"><span>状态：{phase || '正在连接'}</span><span>{messages.length} 条消息</span></div>
      {ended ? <div className="success-card compact"><strong>当前 Session 已结束</strong><p>为避免向下一位访客显示旧内容，本窗口即将关闭。</p></div>
        : error && messages.length === 0 ? <div className="interaction-error">{error}</div>
          : messages.length ? <div className="transcript-list">{messages.map((message, index) => <article key={`${message.timestamp ?? 'm'}-${index}`} className={`transcript-message role-${message.role ?? 'system'}`}><header><strong>{message.role === 'user' ? '访客' : message.role === 'assistant' ? 'Sara' : '系统'}</strong><time>{formatTime(message.timestamp)}</time></header><p>{message.text}</p></article>)}</div>
            : <div className="transcript-empty">当前 Session 还没有可显示的对话内容。</div>}
    </>
  )
}

function SessionTranscript({ conversationId, expectedVisitId }: { conversationId: string; expectedVisitId: string }) {
  return (
    <Shell title="当前对话记录" subtitle="实时接收中间屏幕的新对话，同时用 Backend Session 记录进行校验。">
      <SessionTranscriptBody conversationId={conversationId} expectedVisitId={expectedVisitId} autoClose />
    </Shell>
  )
}

function ResultCenter({ conversationId, visitId }: { conversationId: string; visitId: string }) {
  const [tab, setTab] = useState<'contacts' | 'recordings' | 'transcript'>('contacts')
  const [contacts, setContacts] = useState<ContactRecord[]>([])
  const [recordings, setRecordings] = useState<RecordingRecord[]>([])
  const [databasePath, setDatabasePath] = useState('')
  const [outputDirectory, setOutputDirectory] = useState('')
  const [error, setError] = useState('')

  async function refresh(): Promise<void> {
    try {
      const [contactResponse, recordingResponse] = await Promise.all([
        fetch(`${API_BASE_URL}/api/result-center/contacts?limit=500`),
        fetch(`${API_BASE_URL}/api/result-center/recordings?limit=500`),
      ])
      if (!contactResponse.ok) throw new Error(`联系人读取失败：${contactResponse.status}`)
      if (!recordingResponse.ok) throw new Error(`录音读取失败：${recordingResponse.status}`)
      const contactPayload = await contactResponse.json() as { records?: ContactRecord[]; database_path?: string }
      const recordingPayload = await recordingResponse.json() as { recordings?: RecordingRecord[]; output_directory?: string }
      setContacts(contactPayload.records ?? [])
      setRecordings(recordingPayload.recordings ?? [])
      setDatabasePath(contactPayload.database_path ?? '')
      setOutputDirectory(recordingPayload.output_directory ?? '')
      setError('')
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    }
  }

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => window.clearInterval(timer)
  }, [])

  async function openOutputDirectory(): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/api/result-center/open-output-directory`, { method: 'POST' })
    if (!response.ok) setError((await response.text()) || `打开目录失败：${response.status}`)
  }

  return (
    <Shell title="结果中心" subtitle="查看已登记联系人、现场录音和当前访客 Session。">
      <div className="result-center">
        <div className="result-tabs">
          <button className={tab === 'contacts' ? 'active' : ''} onClick={() => setTab('contacts')}>登记信息 ({contacts.length})</button>
          <button className={tab === 'recordings' ? 'active' : ''} onClick={() => setTab('recordings')}>录音文件 ({recordings.length})</button>
          <button className={tab === 'transcript' ? 'active' : ''} onClick={() => setTab('transcript')}>当前对话</button>
        </div>
        {error ? <div className="interaction-error">{error}</div> : null}

        {tab === 'contacts' ? <>
          <div className="result-toolbar">
            <span>数据库：{databasePath || '正在读取'}</span>
            <div className="result-toolbar-actions">
              <button type="button" onClick={() => void refresh()}>刷新</button>
              <a href={`${API_BASE_URL}/api/result-center/contacts.csv`} download>导出 CSV</a>
            </div>
          </div>
          {contacts.length ? <div className="result-table-wrap"><table className="result-table"><thead><tr><th>登记时间</th><th>姓名</th><th>公司</th><th>邮箱</th><th>电话</th><th>兴趣</th><th>备注</th></tr></thead><tbody>
            {contacts.map((record) => <tr key={record.contact_id}><td>{formatDate(record.created_at)}</td><td>{record.name}</td><td>{record.company || '—'}</td><td>{record.email || '—'}</td><td>{record.phone || '—'}</td><td>{record.interest_tags?.join('、') || '—'}</td><td>{record.notes || '—'}</td></tr>)}
          </tbody></table></div> : <div className="result-empty">还没有登记记录。</div>}
        </> : null}

        {tab === 'recordings' ? <>
          <div className="result-toolbar">
            <span>保存目录：{outputDirectory || '正在读取'}</span>
            <div className="result-toolbar-actions">
              <button type="button" onClick={() => void refresh()}>刷新</button>
              <button type="button" onClick={() => void openOutputDirectory()}>在资源管理器中打开</button>
            </div>
          </div>
          {recordings.length ? <div className="recording-result-list">{recordings.map((record) => <article className="recording-result-card" key={record.filename}><div><strong>{record.filename}</strong><small>{formatDate(record.uploaded_at)} · {formatBytes(record.size_bytes)}</small><small>{record.audio_path}</small></div><audio controls preload="metadata" src={`${API_BASE_URL}${record.artifact_url}`} /><a href={`${API_BASE_URL}${record.artifact_url}`} download>下载</a></article>)}</div> : <div className="result-empty">还没有保存的录音。</div>}
        </> : null}

        {tab === 'transcript' ? <SessionTranscriptBody conversationId={conversationId} expectedVisitId={visitId} autoClose={false} /> : null}
      </div>
    </Shell>
  )
}

export default function InteractionApp() {
  const kind = useMemo(kindFromPath, [])
  const conversationId = query('conversation_id')
  const visitId = query('visit_id')
  if (!conversationId) return <Shell title="无法打开" subtitle="缺少当前会话标识。"><div className="interaction-error">请从中间屏幕上的 Agent 重新打开。</div></Shell>
  if (kind === 'contact') return <ContactRegistration conversationId={conversationId} visitId={visitId} />
  if (kind === 'recording') return <LiveRecording conversationId={conversationId} visitId={visitId} />
  if (kind === 'transcript') return <SessionTranscript conversationId={conversationId} expectedVisitId={visitId} />
  if (kind === 'results') return <ResultCenter conversationId={conversationId} visitId={visitId} />
  return <Shell title="未知功能" subtitle="当前触摸页面不存在。"><button className="primary" onClick={closeWindow}>关闭</button></Shell>
}
