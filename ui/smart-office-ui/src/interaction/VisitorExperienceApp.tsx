import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { INTERACTION_PANEL_CLOSE_MESSAGE } from '../display/multiScreenWindowManager'
import './VisitorExperienceApp.css'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

type ExperienceKind = 'meeting' | 'transcript' | 'results'
type SummaryRecord = {
  summary_id: string
  conversation_id: string
  visit_id: string
  contact_id?: string | null
  status: 'draft' | 'final'
  bullet_points: string[]
  interests: string[]
  actions: string[]
  follow_ups: string[]
  updated_at: string
  finalized_at?: string | null
}
type AvailabilitySlot = {
  slot_id: string
  date: string
  start_at: string
  end_at: string
  start_label: string
  end_label: string
  staff_id: string
  staff_name: string
  staff_role: string
  timezone: string
  availability_source: string
  available: boolean
}
type Booking = {
  booking_id: string
  contact_id?: string | null
  conversation_id: string
  visit_id: string
  staff_id: string
  staff_name: string
  staff_role: string
  meeting_date: string
  start_at: string
  end_at: string
  timezone: string
  topic: string
  status: string
  created_at: string
}
type VisitorProfile = {
  contact_id: string
  conversation_id: string
  visit_id?: string | null
  name: string
  company?: string | null
  email?: string | null
  phone?: string | null
  interest_tags?: string[]
  notes?: string | null
  created_at: string
  updated_at: string
  appointment_count: number
  session_summary_count: number
  next_appointment_at?: string | null
  has_upcoming_appointment: boolean
  latest_activity_at: string
}
type VisitorProfileDetail = {
  contact: VisitorProfile
  appointments: Booking[]
  session_summaries: SummaryRecord[]
}

function query(name: string): string {
  return new URLSearchParams(window.location.search).get(name)?.trim() ?? ''
}

function kindFromPath(): ExperienceKind {
  const path = window.location.pathname.replace(/\/+$/, '')
  if (path.endsWith('/meeting')) return 'meeting'
  if (path.endsWith('/transcript')) return 'transcript'
  return 'results'
}

function closePanel(): void {
  if (window.parent !== window) {
    window.parent.postMessage({ type: INTERACTION_PANEL_CLOSE_MESSAGE }, window.location.origin)
    return
  }
  window.close()
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function Shell({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <main className="visitor-experience-shell">
      <header className="visitor-experience-header">
        <div>
          <span>Smart Office · Visitor Experience</span>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        <button type="button" onClick={closePanel} aria-label="关闭交互面板">×</button>
      </header>
      <section className="visitor-experience-content">{children}</section>
    </main>
  )
}

function SummarySections({ summary }: { summary: SummaryRecord }) {
  return (
    <div className="summary-sections">
      <section className="summary-primary">
        <header>
          <strong>本 Session 要点</strong>
          <span>{summary.status === 'final' ? '已完成' : '持续更新中'}</span>
        </header>
        <ul>
          {summary.bullet_points.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </section>
      <div className="summary-grid">
        <section>
          <strong>关注方向</strong>
          {summary.interests.length
            ? <div className="summary-tags">{summary.interests.map((item) => <span key={item}>{item}</span>)}</div>
            : <p>暂未识别到明确产品方向。</p>}
        </section>
        <section>
          <strong>后续事项</strong>
          {summary.follow_ups.length
            ? <ul>{summary.follow_ups.map((item) => <li key={item}>{item}</li>)}</ul>
            : <p>暂时没有待跟进事项。</p>}
        </section>
      </div>
      <small className="summary-updated">最后更新：{formatDateTime(summary.updated_at)}</small>
    </div>
  )
}

function SessionSummaryPanel({ conversationId, visitId }: { conversationId: string; visitId: string }) {
  const [summary, setSummary] = useState<SummaryRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function refresh(): Promise<void> {
    if (!conversationId || !visitId) return
    try {
      const params = new URLSearchParams({
        conversation_id: conversationId,
        visit_id: visitId,
        language: 'zh',
      })
      const response = await fetch(
        `${API_BASE_URL}/api/visitor-experience/session-summaries/current?${params}`,
      )
      if (!response.ok) throw new Error(`读取 Session 总结失败：${response.status}`)
      const payload = await response.json() as { summary?: SummaryRecord }
      setSummary(payload.summary ?? null)
      setError('')
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3_000)
    const onUpdated = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null
      if (
        String(detail?.conversationId ?? '') === conversationId
        && String(detail?.visitId ?? '') === visitId
      ) void refresh()
    }
    window.addEventListener('smartoffice:session-summary-updated', onUpdated)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('smartoffice:session-summary-updated', onUpdated)
    }
  }, [conversationId, visitId])

  return (
    <Shell
      title="本 Session 对话总结"
      subtitle="仅展示结构化要点，不逐字显示访客与 Sara 的全部对话。"
    >
      {loading ? <div className="experience-empty">正在整理本 Session 要点…</div> : null}
      {error ? <div className="experience-error">{error}</div> : null}
      {!loading && !error && summary ? <SummarySections summary={summary} /> : null}
      {!loading && !error && !summary
        ? <div className="experience-empty">当前 Session 尚未形成可展示的要点。</div>
        : null}
    </Shell>
  )
}

function monthTitle(value: Date): string {
  return value.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long' })
}

function localDateKey(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function calendarDays(month: Date): Array<Date | null> {
  const first = new Date(month.getFullYear(), month.getMonth(), 1)
  const last = new Date(month.getFullYear(), month.getMonth() + 1, 0)
  const mondayOffset = (first.getDay() + 6) % 7
  const values: Array<Date | null> = Array.from({ length: mondayOffset }, () => null)
  for (let day = 1; day <= last.getDate(); day += 1) {
    values.push(new Date(month.getFullYear(), month.getMonth(), day))
  }
  while (values.length % 7) values.push(null)
  return values
}

function MeetingScheduler({ conversationId, visitId }: { conversationId: string; visitId: string }) {
  const today = useMemo(() => {
    const value = new Date()
    return new Date(value.getFullYear(), value.getMonth(), value.getDate())
  }, [])
  const [month, setMonth] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDate, setSelectedDate] = useState('')
  const [slots, setSlots] = useState<AvailabilitySlot[]>([])
  const [selectedSlot, setSelectedSlot] = useState<AvailabilitySlot | null>(null)
  const [booking, setBooking] = useState<Booking | null>(null)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function loadAvailability(day: string): Promise<void> {
    setSelectedDate(day)
    setSelectedSlot(null)
    setBooking(null)
    setLoading(true)
    setError('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/visitor-experience/meeting-availability?date=${encodeURIComponent(day)}`,
      )
      if (!response.ok) throw new Error((await response.text()) || `读取时间失败：${response.status}`)
      const payload = await response.json() as { slots?: AvailabilitySlot[] }
      setSlots(payload.slots ?? [])
    } catch (value) {
      setSlots([])
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      setLoading(false)
    }
  }

  async function confirmBooking(): Promise<void> {
    if (!selectedSlot || !selectedDate || submitting) return
    setSubmitting(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/visitor-experience/meeting-bookings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          conversation_id: conversationId,
          visit_id: visitId,
          date: selectedDate,
          slot_id: selectedSlot.slot_id,
          topic: 'Smart Office 产品演示',
        }),
      })
      if (!response.ok) throw new Error((await response.text()) || `预约失败：${response.status}`)
      const payload = await response.json() as { booking?: Booking }
      if (!payload.booking) throw new Error('Backend 未返回预约记录。')
      setBooking(payload.booking)
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      setSubmitting(false)
    }
  }

  const days = useMemo(() => calendarDays(month), [month])
  const selectedLabel = selectedDate
    ? new Date(`${selectedDate}T12:00:00`).toLocaleDateString('zh-CN', {
        year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
      })
    : ''

  return (
    <Shell
      title="预约会议"
      subtitle="先选择日期，再从绿色时间段中选择一位可用员工。当前为稳定的展会模拟日程。"
    >
      <div className="meeting-layout">
        <section className="calendar-card">
          <header className="calendar-header">
            <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button>
            <strong>{monthTitle(month)}</strong>
            <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button>
          </header>
          <div className="calendar-weekdays">
            {['一', '二', '三', '四', '五', '六', '日'].map((item) => <span key={item}>{item}</span>)}
          </div>
          <div className="calendar-grid">
            {days.map((day, index) => {
              if (!day) return <span key={`empty-${index}`} className="calendar-empty" />
              const key = localDateKey(day)
              const past = day.getTime() < today.getTime()
              return (
                <button
                  key={key}
                  type="button"
                  disabled={past}
                  className={selectedDate === key ? 'selected' : ''}
                  onClick={() => void loadAvailability(key)}
                >
                  {day.getDate()}
                </button>
              )
            })}
          </div>
        </section>

        <section className="availability-card">
          <header>
            <strong>{selectedLabel || '请选择日期'}</strong>
            <span>绿色表示可预约</span>
          </header>
          {loading ? <div className="experience-empty">正在生成模拟可用时间…</div> : null}
          {!loading && selectedDate && slots.length === 0
            ? <div className="experience-empty">当天没有可用模拟时间，请选择其他日期。</div>
            : null}
          <div className="availability-list">
            {slots.map((slot) => (
              <button
                key={slot.slot_id}
                type="button"
                disabled={!slot.available || Boolean(booking)}
                className={`${slot.available ? 'available' : 'booked'} ${selectedSlot?.slot_id === slot.slot_id ? 'selected' : ''}`}
                onClick={() => setSelectedSlot(slot)}
              >
                <strong>{slot.start_label}–{slot.end_label}</strong>
                <span>{slot.staff_name}</span>
                <small>{slot.staff_role}</small>
                <em>{slot.available ? '可预约' : '已被预约'}</em>
              </button>
            ))}
          </div>

          {selectedSlot && !booking ? (
            <div className="booking-confirmation">
              <strong>确认本次预约</strong>
              <dl>
                <div><dt>日期</dt><dd>{selectedLabel}</dd></div>
                <div><dt>时间</dt><dd>{selectedSlot.start_label}–{selectedSlot.end_label}</dd></div>
                <div><dt>员工</dt><dd>{selectedSlot.staff_name} · {selectedSlot.staff_role}</dd></div>
                <div><dt>主题</dt><dd>Smart Office 产品演示</dd></div>
              </dl>
              <div>
                <button type="button" className="secondary" onClick={() => setSelectedSlot(null)}>返回选择</button>
                <button type="button" className="primary" disabled={submitting} onClick={() => void confirmBooking()}>
                  {submitting ? '正在确认…' : '确认预约'}
                </button>
              </div>
            </div>
          ) : null}

          {booking ? (
            <div className="booking-success">
              <b>✓</b>
              <strong>预约成功</strong>
              <p>{formatDateTime(booking.start_at)}，{booking.staff_name} 将为您提供演示。</p>
              <small>预约编号：{booking.booking_id}</small>
              {!booking.contact_id
                ? <p>为了便于工作人员联系，请继续填写右侧的“登记信息”。</p>
                : null}
            </div>
          ) : null}
          {error ? <div className="experience-error">{error}</div> : null}
        </section>
      </div>
    </Shell>
  )
}

function VisitorProfileDetailView({ detail }: { detail: VisitorProfileDetail }) {
  const contact = detail.contact
  return (
    <div className="profile-detail">
      <header className="profile-detail-heading">
        <div>
          <span>Visitor Profile</span>
          <h2>{contact.name}</h2>
          <p>{contact.company || '未填写公司 / 机构'}</p>
        </div>
        {contact.has_upcoming_appointment ? <strong>已预约</strong> : <span>暂无预约</span>}
      </header>

      <section className="profile-section">
        <h3>基本信息</h3>
        <dl className="profile-fields">
          <div><dt>邮箱</dt><dd>{contact.email || '—'}</dd></div>
          <div><dt>电话</dt><dd>{contact.phone || '—'}</dd></div>
          <div><dt>登记时间</dt><dd>{formatDateTime(contact.created_at)}</dd></div>
          <div><dt>兴趣方向</dt><dd>{contact.interest_tags?.join('、') || '—'}</dd></div>
          <div className="wide"><dt>备注</dt><dd>{contact.notes || '—'}</dd></div>
        </dl>
      </section>

      <section className="profile-section">
        <h3>预约信息</h3>
        {detail.appointments.length ? (
          <div className="appointment-list">
            {detail.appointments.map((item) => (
              <article key={item.booking_id}>
                <div>
                  <strong>{formatDateTime(item.start_at)}</strong>
                  <span>{item.staff_name} · {item.staff_role}</span>
                </div>
                <small>{item.topic}</small>
                <em>{item.status === 'confirmed' ? '已确认' : item.status}</em>
              </article>
            ))}
          </div>
        ) : <div className="experience-empty compact">该访客还没有预约记录。</div>}
      </section>

      <section className="profile-section">
        <h3>Session 对话总结</h3>
        {detail.session_summaries.length ? (
          <div className="profile-summary-list">
            {detail.session_summaries.map((summary) => (
              <article key={summary.summary_id}>
                <header>
                  <strong>{formatDateTime(summary.updated_at)}</strong>
                  <span>{summary.status === 'final' ? '最终总结' : '草稿'}</span>
                </header>
                <ul>{summary.bullet_points.map((item) => <li key={item}>{item}</li>)}</ul>
              </article>
            ))}
          </div>
        ) : <div className="experience-empty compact">该访客暂时没有 Session 总结。</div>}
      </section>
    </div>
  )
}

function EnhancedResultCenter() {
  const [profiles, setProfiles] = useState<VisitorProfile[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<VisitorProfileDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function loadProfiles(): Promise<void> {
    try {
      const response = await fetch(`${API_BASE_URL}/api/result-center/visitor-profiles?limit=500`)
      if (!response.ok) throw new Error(`读取访客档案失败：${response.status}`)
      const payload = await response.json() as { profiles?: VisitorProfile[] }
      const next = payload.profiles ?? []
      setProfiles(next)
      const nextSelected = selectedId || next[0]?.contact_id || ''
      setSelectedId(nextSelected)
      setError('')
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      setLoading(false)
    }
  }

  async function loadDetail(contactId: string): Promise<void> {
    if (!contactId) {
      setDetail(null)
      return
    }
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/result-center/visitor-profiles/${encodeURIComponent(contactId)}`,
      )
      if (!response.ok) throw new Error(`读取访客详情失败：${response.status}`)
      const payload = await response.json() as VisitorProfileDetail
      setDetail(payload)
      setError('')
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    }
  }

  useEffect(() => {
    void loadProfiles()
    const timer = window.setInterval(() => void loadProfiles(), 5_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => { void loadDetail(selectedId) }, [selectedId])

  return (
    <Shell
      title="结果中心 · 访客档案"
      subtitle="有未来预约的访客优先显示；点击访客可查看登记信息、预约和每个 Session 的总结。"
    >
      {error ? <div className="experience-error">{error}</div> : null}
      {loading ? <div className="experience-empty">正在读取访客档案…</div> : null}
      {!loading && !profiles.length
        ? <div className="experience-empty">还没有登记过的访客。</div>
        : null}
      {profiles.length ? (
        <div className="profile-center-layout">
          <aside className="profile-list">
            <header>
              <strong>访客列表</strong>
              <button type="button" onClick={() => void loadProfiles()}>刷新</button>
            </header>
            {profiles.map((profile) => (
              <button
                key={profile.contact_id}
                type="button"
                className={selectedId === profile.contact_id ? 'selected' : ''}
                onClick={() => setSelectedId(profile.contact_id)}
              >
                <div>
                  <strong>{profile.name}</strong>
                  {profile.has_upcoming_appointment ? <em>已预约</em> : null}
                </div>
                <span>{profile.company || '未填写公司'}</span>
                <small>
                  {profile.next_appointment_at
                    ? `${formatDateTime(profile.next_appointment_at)} · ${profile.appointment_count} 个预约`
                    : `暂无预约 · ${profile.session_summary_count} 个 Session 总结`}
                </small>
              </button>
            ))}
          </aside>
          <section className="profile-detail-wrap">
            {detail
              ? <VisitorProfileDetailView detail={detail} />
              : <div className="experience-empty">请选择一位访客。</div>}
          </section>
        </div>
      ) : null}
    </Shell>
  )
}

export default function VisitorExperienceApp() {
  const kind = kindFromPath()
  const conversationId = query('conversation_id')
  const visitId = query('visit_id')
  if (kind === 'results') return <EnhancedResultCenter />
  if (!conversationId || !visitId) {
    return (
      <Shell title="无法打开" subtitle="缺少当前 Session 标识。">
        <div className="experience-error">请从 Sara 主界面重新打开该功能。</div>
      </Shell>
    )
  }
  if (kind === 'meeting') {
    return <MeetingScheduler conversationId={conversationId} visitId={visitId} />
  }
  return <SessionSummaryPanel conversationId={conversationId} visitId={visitId} />
}
