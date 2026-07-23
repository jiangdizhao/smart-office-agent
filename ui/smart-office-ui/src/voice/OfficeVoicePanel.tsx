import { useEffect, useRef, useState } from 'react'
import { BrowserSpeechCapture } from './browserSpeechRecognition'
import { realtimeAgent, type RealtimeRuntimeStatus, type VoiceLanguage } from './realtimeAgentRuntime'
import { realtimeOfficeInterpreter, type RealtimeOfficeToolCall } from './realtimeOfficeInterpreter'
import { voiceOutputManager, type VoiceOutputProvider } from './voiceOutputManager'
import './VoiceDebugPanel.css'

const API = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const TASK_TIMEOUT = 180_000
const CJK = /[\u3400-\u9fff]/

type Actor = 'visitor' | 'employee' | 'operator'
type Asr = 'realtime' | 'browser'
type PanelState = 'idle' | 'connecting' | 'listening' | 'processing' | 'speaking' | 'error'
type TaskStatus = 'created' | 'planning' | 'running' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled'
type RecipientEntry = { key: string; name: string; email: string }
type OfficeStatus = {
  presentation_open?: boolean
  slideshow_active?: boolean
  current_slide?: number | null
  total_slides?: number | null
  target_monitor_device?: string | null
  slideshow_monitor_device?: string | null
  monitor_placement_enforced?: boolean
  volume_percent?: number | null
  brightness_percent?: number | null
  summary_path_relative?: string | null
  artifact_url?: string | null
  outlook_draft_created?: boolean
  outlook_draft_verified?: boolean
  outlook_draft_entry_id?: string | null
  outlook_draft_displayed?: boolean
  source_outlook_draft_entry_id?: string | null
  sender_account_email?: string | null
  default_recipient_key?: string | null
  recipient_key?: string | null
  recipient_name?: string | null
  recipient_email?: string | null
  recipient_catalog?: RecipientEntry[]
  allowed_recipient_keys?: string[]
  approval_gated_email_send_enabled?: boolean
  unrestricted_email_send_enabled?: boolean
  draft_notice_removed?: boolean
  send_invoked?: boolean
  sent?: boolean
  delivery_confirmed?: boolean
}
type ToolData = {
  office_status?: OfficeStatus
  presentation_status?: OfficeStatus
  verification?: { ok?: boolean; message?: string }
  artifact_url?: string
  summary_path_relative?: string
  outlook_draft_created?: boolean
  outlook_draft_verified?: boolean
  outlook_draft_entry_id?: string
  outlook_draft_displayed?: boolean
  source_outlook_draft_entry_id?: string
  sender_account_email?: string
  recipient_key?: string
  recipient_name?: string
  recipient_email?: string
  approval_gated_email_send_enabled?: boolean
  unrestricted_email_send_enabled?: boolean
  draft_notice_removed?: boolean
  send_invoked?: boolean
  sent?: boolean
  delivery_confirmed?: boolean
}
type TaskStep = {
  index: number
  tool_name?: string | null
  args?: { recipient_key?: string }
  status: string
  result?: { tool_name: string; ok: boolean; message: string; data?: ToolData } | null
}
type Task = { task_id: string; status: TaskStatus; steps: TaskStep[]; summary?: string | null }
type Turn = {
  route: string
  spoken_text: string
  scene: string
  permission_decision: string
  task_id: string | null
  task_status: string | null
  approval_required: boolean
  content_url: string | null
  source_ids?: string[]
  realtime_tool_call?: RealtimeOfficeToolCall | null
  tool_result?: { tool_name: string; ok: boolean; message: string; data?: ToolData } | null
  verification_result?: { ok: boolean; message: string } | null
  office_status?: OfficeStatus | null
  presentation_status?: OfficeStatus | null
}

function cid(): string {
  const key = 'smartoffice_debug_conversation_id'
  const old = sessionStorage.getItem(key)
  if (old) return old
  const value = `debug-${crypto.randomUUID()}`
  sessionStorage.setItem(key, value)
  return value
}
function runtimeInitial(): RealtimeRuntimeStatus {
  return { connected: false, connectionState: 'not-created', dataChannelState: 'not-created', microphoneAttached: false, responseActive: false, outputActive: false }
}
function utteranceLanguage(text: string, selected: VoiceLanguage): VoiceLanguage {
  return CJK.test(text) ? 'zh' : /[A-Za-z]/.test(text) ? 'en' : selected
}
function latestStep(task: Task): TaskStep | undefined {
  return [...task.steps].reverse().find((step) => step.result)
}
function waitingStep(task: Task): TaskStep | undefined {
  return task.steps.find((step) => step.status === 'waiting_approval')
}
function statusFromTask(task: Task): OfficeStatus | null {
  const data = latestStep(task)?.result?.data
  return data?.office_status ?? data?.presentation_status ?? null
}
function artifactFromTask(task: Task): string | null {
  for (const step of [...task.steps].reverse()) {
    const data = step.result?.data
    if (data?.artifact_url) return data.artifact_url
    if (data?.office_status?.artifact_url) return data.office_status.artifact_url
  }
  return null
}
function finalTaskText(task: Task, lang: VoiceLanguage): string {
  const done = task.steps.filter((step) => step.status === 'succeeded').length
  const step = latestStep(task)
  const data = step?.result?.data
  const status = data?.office_status ?? data?.presentation_status
  if (task.status === 'cancelled') return lang === 'zh' ? `办公任务已取消，已完成 ${done} 个步骤。` : `The office task was cancelled after ${done} completed steps.`
  if (task.status === 'failed') {
    const failed = task.steps.find((item) => item.status === 'failed')?.index ?? 'unknown'
    return lang === 'zh' ? `办公任务在第 ${failed} 步失败，后续步骤没有执行。` : `The office task failed at step ${failed}; later steps were not executed.`
  }
  if (data?.sent || status?.sent) {
    const sender = data?.sender_account_email ?? status?.sender_account_email ?? 'configured Outlook account'
    const recipientName = data?.recipient_name ?? status?.recipient_name ?? data?.recipient_key ?? status?.recipient_key ?? 'configured recipient'
    const recipientEmail = data?.recipient_email ?? status?.recipient_email ?? 'configured email'
    return lang === 'zh'
      ? `已在第二次批准后删除“仅保存为草稿、尚未发送”的提示，并由 Outlook 接受从 ${sender} 发往 ${recipientName}（${recipientEmail}）的发送操作。最终送达状态由 Outlook 和网络处理。`
      : `After the second approval, the draft-only notice was removed and Outlook accepted the send from ${sender} to ${recipientName} (${recipientEmail}). Final delivery is handled by Outlook and the network.`
  }
  if (data?.outlook_draft_created || status?.outlook_draft_created) {
    const sender = data?.sender_account_email ?? status?.sender_account_email ?? 'configured Outlook account'
    const recipientName = data?.recipient_name ?? status?.recipient_name ?? data?.recipient_key ?? status?.recipient_key ?? 'configured recipient'
    const recipientEmail = data?.recipient_email ?? status?.recipient_email ?? 'configured email'
    return lang === 'zh'
      ? `Outlook 草稿已创建并验证。发件账号为 ${sender}，收件人为 ${recipientName}（${recipientEmail}），邮件尚未发送。`
      : `A verified Outlook draft was created from ${sender} for ${recipientName} (${recipientEmail}); it has not been sent.`
  }
  const summary = data?.summary_path_relative ?? status?.summary_path_relative
  if (summary) return lang === 'zh' ? `演示摘要已生成：${summary}。` : `The presentation summary was generated at ${summary}.`
  if (status?.volume_percent !== undefined || status?.brightness_percent !== undefined) {
    return lang === 'zh' ? `办公任务已完成。当前音量 ${status?.volume_percent ?? '不可用'}%，亮度 ${status?.brightness_percent ?? '不可用'}%。` : `The office task completed. Volume is ${status?.volume_percent ?? 'unavailable'}% and brightness is ${status?.brightness_percent ?? 'unavailable'}%.`
  }
  if (status?.slideshow_active) return lang === 'zh' ? `任务已完成。当前第 ${status.current_slide} 页，共 ${status.total_slides} 页。` : `The task completed. The slide show is on slide ${status.current_slide} of ${status.total_slides}.`
  return lang === 'zh' ? `已完成并验证 ${task.steps.length} 个办公步骤。` : `Completed and verified ${task.steps.length} office steps.`
}

export default function OfficeVoicePanel() {
  const browser = useRef(new BrowserSpeechCapture())
  const generation = useRef(0)
  const approvalPrompted = useRef<string | null>(null)
  const [language, setLanguage] = useState<VoiceLanguage>('zh')
  const [actor, setActor] = useState<Actor>((localStorage.getItem('smartoffice_actor_type') as Actor) || 'visitor')
  const [asr, setAsr] = useState<Asr>(localStorage.getItem('smartoffice_asr_provider') === 'browser' ? 'browser' : 'realtime')
  const [voice, setVoice] = useState<VoiceOutputProvider>(voiceOutputManager.selectedProvider())
  const [panel, setPanel] = useState<PanelState>('idle')
  const [runtime, setRuntime] = useState<RealtimeRuntimeStatus>(runtimeInitial)
  const [input, setInput] = useState('')
  const [transcript, setTranscript] = useState('')
  const [answer, setAnswer] = useState('')
  const [route, setRoute] = useState('')
  const [permission, setPermission] = useState('')
  const [tool, setTool] = useState('')
  const [verified, setVerified] = useState<boolean | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null)
  const [pendingApprovalTool, setPendingApprovalTool] = useState<string | null>(null)
  const [pendingRecipientKey, setPendingRecipientKey] = useState<string | null>(null)
  const [office, setOffice] = useState<OfficeStatus | null>(null)
  const [contentUrl, setContentUrl] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(true)
  const listening = panel === 'listening'
  const busy = ['connecting', 'processing', 'speaking'].includes(panel)
  const active = Boolean(taskId && taskStatus && !['completed', 'failed', 'cancelled'].includes(taskStatus))

  useEffect(() => {
    const timer = window.setInterval(() => setRuntime(realtimeAgent.status()), 500)
    return () => { window.clearInterval(timer); generation.current += 1 }
  }, [])

  async function speak(text: string, lang: VoiceLanguage) {
    if (voice === 'none') { setPanel('idle'); return }
    setPanel('speaking')
    await voiceOutputManager.speak(text, lang)
    setPanel('idle')
  }
  async function connect() {
    setPanel('connecting'); setError('')
    try { await realtimeAgent.prewarm(language); setRuntime(realtimeAgent.status()); setPanel('idle') }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setPanel('error') }
  }
  async function begin() {
    setError(''); setTranscript(''); setAnswer(''); await voiceOutputManager.stop()
    try {
      if (asr === 'realtime') await realtimeAgent.beginCapture(language)
      else await browser.current.begin(language, setTranscript)
      setPanel('listening')
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setPanel('error') }
  }
  async function end() {
    setPanel('processing')
    try {
      const text = asr === 'realtime' ? await realtimeAgent.endCapture() : await browser.current.end()
      setTranscript(text); await submit(text, 'voice')
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setPanel('error') }
  }
  async function monitor(id: string, lang: VoiceLanguage, token: number) {
    const deadline = performance.now() + TASK_TIMEOUT
    while (performance.now() < deadline && generation.current === token) {
      const response = await fetch(`${API}/agent/tasks/${encodeURIComponent(id)}`)
      if (!response.ok) throw new Error(`Task status failed: ${response.status}`)
      const task = (await response.json()) as Task
      if (generation.current !== token) return
      setTaskStatus(task.status)
      const state = statusFromTask(task); if (state) setOffice(state)
      const step = latestStep(task)
      if (step?.result) { setTool(step.result.tool_name); setVerified(step.result.data?.verification?.ok ?? null) }
      const artifact = artifactFromTask(task); if (artifact) setContentUrl(artifact)
      const waiting = waitingStep(task)
      if (task.status === 'waiting_approval' && waiting) {
        const recipientKey = waiting.args?.recipient_key ?? null
        const approvalKey = `${id}:${waiting.index}:${waiting.tool_name ?? 'unknown'}:${recipientKey ?? 'latest'}`
        setPendingApprovalTool(waiting.tool_name ?? null)
        setPendingRecipientKey(recipientKey)
        if (approvalPrompted.current !== approvalKey) {
          approvalPrompted.current = approvalKey
          const isSend = waiting.tool_name === 'outlook_send_approved_draft'
          const recipientLabel = recipientKey ? `联系人 ${recipientKey}` : '最新已验证草稿的联系人'
          const prompt = lang === 'zh'
            ? isSend
              ? `发送给${recipientLabel}的邮件需要第二次批准。批准后会先删除正文中的“该邮件目前仅保存为 Outlook 草稿，尚未发送。”，重新保存并核对发件人与白名单收件人，然后调用 Outlook 发送。请检查草稿后再批准。`
              : `为${recipientLabel}创建本机 Outlook 草稿需要第一次批准。草稿会使用已登录的 Outlook 账号，但此时不会发送。`
            : isSend
              ? `Sending to ${recipientLabel} requires a second approval. The draft-only notice will be removed and the allowlisted recipient will be re-verified before Outlook Send() is invoked.`
              : `Creating the Outlook draft for ${recipientLabel} requires the first approval. It will not send at this stage.`
          setAnswer(prompt); if (!realtimeAgent.status().microphoneAttached) await speak(prompt, lang)
        }
      }
      if (['completed', 'failed', 'cancelled'].includes(task.status)) {
        setPendingApprovalTool(null)
        setPendingRecipientKey(null)
        const text = finalTaskText(task, lang); setAnswer(text)
        if (!realtimeAgent.status().microphoneAttached && task.status !== 'cancelled') await speak(text, lang)
        return
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250))
    }
    if (generation.current === token) throw new Error('The office task did not finish within three minutes.')
  }
  async function submit(text: string, source: 'text' | 'voice') {
    const clean = text.trim(); if (!clean) throw new Error('请输入文字或完成一次语音识别。')
    setPanel('processing'); setError('')
    const lang = utteranceLanguage(clean, language)
    const decision = await realtimeOfficeInterpreter.interpret(clean, lang)
    if (decision.kind === 'clarify') { const q = decision.clarification || (lang === 'zh' ? '请明确办公操作或选择已配置的邮件联系人。' : 'Please clarify the office action or choose a configured email recipient.'); setAnswer(q); await speak(q, lang); return }
    if (active && decision.kind === 'tool_call') { const text = lang === 'zh' ? '当前已有办公任务正在执行。请先等待、批准、跳过或取消。' : 'An office task is already active. Wait, approve, skip, or cancel it first.'; setAnswer(text); await speak(text, lang); return }
    const call = decision.kind === 'tool_call' ? decision.toolCall : null
    const endpoint = call?.name === 'office_plan' ? '/agent/office-turn' : '/agent/turn'
    const response = await fetch(`${API}${endpoint}`, { method: 'POST', headers: { 'Content-Type': 'application/json; charset=utf-8' }, body: JSON.stringify({ conversation_id: cid(), text: clean, language: lang, input_source: source, actor_context: { type: actor, source: 'phase3_gate3_5_panel' }, active_task_id: active ? taskId : null, realtime_tool_call: call }) })
    if (!response.ok) throw new Error(`Agent turn failed: ${response.status} ${await response.text()}`)
    const payload = (await response.json()) as Turn
    const safe = lang === 'en' && CJK.test(payload.spoken_text) ? 'The office request was processed. Check the verified status shown on screen.' : payload.spoken_text
    setRoute(payload.route); setPermission(payload.permission_decision); setAnswer(safe); setTool(payload.tool_result?.tool_name ?? call?.name ?? ''); setVerified(payload.verification_result?.ok ?? null); setOffice(payload.office_status ?? payload.presentation_status ?? null); setContentUrl(payload.content_url)
    if (payload.task_id) setTaskId(payload.task_id)
    if (payload.task_status) setTaskStatus(payload.task_status as TaskStatus)
    if (payload.route === 'office_planned_task' && payload.task_id && payload.tool_result?.ok) { const token = generation.current + 1; generation.current = token; await speak(safe, lang); void monitor(payload.task_id, lang, token).catch((e) => { setError(e instanceof Error ? e.message : String(e)); setPanel('error') }); return }
    await speak(safe, lang)
  }
  async function approve(action: 'approve' | 'skip' | 'cancel') {
    if (!taskId) return
    if (action === 'cancel') { await fetch(`${API}/agent/tasks/${taskId}/cancel`, { method: 'POST' }); setAnswer('已请求取消当前任务。'); return }
    const response = await fetch(`${API}/agent/tasks/${taskId}/approval`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, note: 'Submitted from Phase 3 office panel' }) })
    if (!response.ok) throw new Error(`Approval failed: ${response.status} ${await response.text()}`)
    const isSend = pendingApprovalTool === 'outlook_send_approved_draft'
    const recipientText = pendingRecipientKey ? `联系人 ${pendingRecipientKey}` : '所选联系人'
    setAnswer(action === 'approve' ? (isSend ? `已完成第二次批准，正在删除草稿提示并向${recipientText}发送。` : `已完成第一次批准，正在为${recipientText}创建 Outlook 草稿。`) : (isSend ? '已跳过 Outlook 发送步骤，草稿保持未发送。' : '已跳过 Outlook 草稿步骤。'))
  }

  const recipientCatalog = office?.recipient_catalog ?? []
  const recipientCatalogText = recipientCatalog.length
    ? recipientCatalog.map((item) => `${item.name} [${item.key}]: ${item.email}`).join(' · ')
    : 'Rico [rico]: jiangdizhao@gmail.com'
  const stateLabel: Record<PanelState, string> = { idle: '空闲', connecting: '正在连接', listening: '正在聆听', processing: '正在处理', speaking: '正在朗读', error: '发生错误' }
  return <aside className={`voice-debug-panel ${expanded ? 'expanded' : 'collapsed'}`}>
    <button className="voice-panel-toggle" onClick={() => setExpanded(!expanded)}>{expanded ? '收起 Phase 3 控制台' : '打开 Phase 3 控制台'}</button>
    {expanded ? <div className="voice-panel-content">
      <div className="voice-panel-heading"><div><span className="voice-kicker">M3A-Fusion · Gate 3–5</span><strong>演示、设备、摘要与 Outlook 邮件</strong></div><span className={`voice-state state-${panel}`}>{stateLabel[panel]}</span></div>
      <div className="voice-settings-grid">
        <label>语言<select value={language} disabled={listening || busy} onChange={(e) => setLanguage(e.target.value as VoiceLanguage)}><option value="zh">中文</option><option value="en">English</option></select></label>
        <label>身份<select value={actor} disabled={listening || busy} onChange={(e) => { const value = e.target.value as Actor; setActor(value); localStorage.setItem('smartoffice_actor_type', value) }}><option value="visitor">Visitor</option><option value="employee">Employee</option><option value="operator">Operator</option></select></label>
        <label>语音识别<select value={asr} disabled={listening || busy} onChange={(e) => { const value = e.target.value as Asr; setAsr(value); localStorage.setItem('smartoffice_asr_provider', value) }}><option value="realtime">GPT Realtime</option><option value="browser" disabled={!browser.current.available()}>Browser ASR</option></select></label>
        <label>语音输出<select value={voice} disabled={listening || busy} onChange={(e) => { const value = e.target.value as VoiceOutputProvider; void voiceOutputManager.setProvider(value).then(() => setVoice(value)) }}><option value="realtime">GPT Realtime</option><option value="none">仅文字</option></select></label>
      </div>
      <div className="voice-connection-row"><span>Voice WebRTC: {runtime.connectionState} / {runtime.dataChannelState}</span><span>Mic: {runtime.microphoneAttached ? 'attached' : 'released'}</span><button disabled={listening || busy || runtime.connected} onClick={() => void connect()}>{runtime.connected ? '已连接' : '连接语音'}</button></div>
      <div className="voice-ptt-row"><button className={listening ? 'voice-ptt listening' : 'voice-ptt'} disabled={busy} onClick={() => void (listening ? end() : begin())}>{listening ? '结束说话' : '点击说话'}</button><button className="voice-stop" disabled={!runtime.outputActive && panel !== 'speaking'} onClick={() => void voiceOutputManager.stop().then(() => setPanel('idle'))}>停止朗读</button><button disabled={taskStatus !== 'waiting_approval'} onClick={() => void approve('approve')}>{pendingApprovalTool === 'outlook_send_approved_draft' ? '第二次批准并发送' : '第一次批准并创建草稿'}</button><button disabled={taskStatus !== 'waiting_approval'} onClick={() => void approve('skip')}>跳过</button><button className="voice-stop" disabled={!active} onClick={() => void approve('cancel')}>取消任务</button></div>
      <div className="voice-text-test"><input value={input} disabled={listening || busy} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !busy) void submit(input, 'text').catch((x) => { setError(String(x)); setPanel('error') }) }} placeholder="输入演示、设备、摘要或发给已配置联系人的 Outlook 邮件命令"/><button disabled={listening || busy} onClick={() => void submit(input, 'text').catch((x) => { setError(x instanceof Error ? x.message : String(x)); setPanel('error') })}>发送</button></div>
      <div className="voice-result-grid"><div><span>识别文本</span><p>{transcript || '—'}</p></div><div><span>Agent 文本</span><p>{answer || '—'}</p></div></div>
      <div className="voice-result-grid"><div><span>PowerPoint</span><p>{office?.slideshow_active ? `Presenting ${office.current_slide}/${office.total_slides}` : office?.presentation_open ? 'Ready' : 'Closed'}</p></div><div><span>设备</span><p>Volume {office?.volume_percent ?? '—'}% · Brightness {office?.brightness_percent ?? '—'}%</p></div></div>
      <div className="voice-result-grid"><div><span>Outlook 发件账号</span><p>{office?.sender_account_email ?? 'jiangdizhao1@outlook.com'}</p></div><div><span>本次/默认收件人</span><p>{office?.recipient_name ?? 'Rico'} [{office?.recipient_key ?? office?.default_recipient_key ?? 'rico'}]: {office?.recipient_email ?? 'jiangdizhao@gmail.com'}</p></div></div>
      <div className="voice-result-grid"><div><span>允许的邮件联系人</span><p>{recipientCatalogText}</p></div><div><span>发送边界</span><p>仅白名单联系人 · 发送前第二次批准</p></div></div>
      <div className="voice-ptt-row">{contentUrl ? <button onClick={() => window.open(`${API}${contentUrl}`, '_blank', 'noopener,noreferrer')}>打开摘要</button> : null}</div>
      <div className="voice-diagnostics"><span>Actor: {actor}</span><span>Route: {route || '—'}</span><span>Permission: {permission || '—'}</span><span>Tool: {tool || '—'}</span><span>Verification: {verified === null ? '—' : verified ? 'PASS' : 'FAIL'}</span><span>Task: {taskId || '—'}</span><span>Task status: {taskStatus || '—'}</span><span>Email send: allowlist + second approval</span></div>
      {error ? <div className="voice-error">{error}</div> : null}
      <p className="voice-safety-note">本机 Classic Outlook 发件账号固定为 jiangdizhao1@outlook.com。收件人从 Backend 白名单中按联系人键名选择；创建草稿和发送邮件分别需要两次独立批准。第二次批准后会先删除“仅保存为草稿、尚未发送”的提示，再调用 Outlook 发送。模型不能直接提供任意邮箱地址。</p>
    </div> : null}
  </aside>
}
