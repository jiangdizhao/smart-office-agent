import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'
import { voiceOutputManager } from './voiceOutputManager'
import { visitLeaseRegistry, type VisitLease } from '../vision/visitLeaseRegistry'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const NOTIFICATION_IDLE_POLL_MS = 250
const NOTIFICATION_IDLE_TIMEOUT_MS = 20_000

export type ManagedBackgroundAction =
  | 'music_play_random'
  | 'music_stop'
  | 'teams_open'
  | 'teams_close'
  | 'onenote_open'
  | 'onenote_close'

export type BackgroundTaskAcceptance = {
  taskId: string
  action: ManagedBackgroundAction
  acceptedText: string
}

type LegacyToolResult = {
  tool_name?: string
  ok?: boolean
  message?: string
  data?: Record<string, unknown>
}

type LegacyTaskStep = {
  result?: LegacyToolResult | null
}

type LegacyTaskSession = {
  task_id?: string
  status?: string
  summary?: string | null
  steps?: LegacyTaskStep[]
}

type StartRequest = {
  conversationId: string
  visitId: string | null
  actor: 'visitor' | 'employee' | 'operator'
  language: VoiceLanguage
  lease: VisitLease | null
}

function canonicalCommand(action: ManagedBackgroundAction): string {
  const commands: Record<ManagedBackgroundAction, string> = {
    music_play_random: '播放音乐',
    music_stop: '停止音乐',
    teams_open: '打开 Teams',
    teams_close: '关闭 Teams',
    onenote_open: '打开 OneNote',
    onenote_close: '关闭 OneNote',
  }
  return commands[action]
}

function acceptedText(action: ManagedBackgroundAction, language: VoiceLanguage): string {
  const labels: Record<ManagedBackgroundAction, { zh: string; en: string }> = {
    music_play_random: { zh: '正在随机播放一首本地音乐', en: 'I am starting a randomly selected local track' },
    music_stop: { zh: '正在停止音乐', en: 'I am stopping the music' },
    teams_open: { zh: '正在打开 Microsoft Teams', en: 'I am opening Microsoft Teams' },
    teams_close: { zh: '正在关闭 Microsoft Teams', en: 'I am closing Microsoft Teams' },
    onenote_open: { zh: '正在打开 OneNote', en: 'I am opening OneNote' },
    onenote_close: { zh: '正在关闭 OneNote', en: 'I am closing OneNote' },
  }
  return language === 'zh'
    ? `${labels[action].zh}，我会在后台验证结果；您可以继续和我交流。`
    : `${labels[action].en} and will verify it in the background. You can keep talking to me.`
}

function latestResult(task: LegacyTaskSession): LegacyToolResult | null {
  return [...(task.steps ?? [])].reverse().find((step) => step.result)?.result ?? null
}

function bool(value: unknown): boolean {
  return value === true
}

function completionText(
  action: ManagedBackgroundAction,
  task: LegacyTaskSession,
  language: VoiceLanguage,
): string {
  const result = latestResult(task)
  const data = result?.data ?? {}
  if (task.status !== 'completed' || result?.ok !== true) {
    const detail = String(result?.message ?? task.summary ?? '').trim()
    return language === 'zh'
      ? `后台操作没有完成。${detail || '请检查应用安装和本机配置。'}`
      : `The background action did not complete. ${detail || 'Please check the application installation and local configuration.'}`
  }

  if (action === 'music_play_random') {
    const track = String(data.selected_track_name ?? '').trim()
    return language === 'zh'
      ? `音乐已经开始播放${track ? `：《${track}》` : '。'}`
      : `The music is now playing${track ? `: ${track}.` : '.'}`
  }
  if (action === 'music_stop') {
    return language === 'zh' ? '音乐已经停止。' : 'The music has stopped.'
  }

  const labels: Record<Exclude<ManagedBackgroundAction, 'music_play_random' | 'music_stop'>, string> = {
    teams_open: 'Microsoft Teams',
    teams_close: 'Microsoft Teams',
    onenote_open: 'OneNote',
    onenote_close: 'OneNote',
  }
  const label = labels[action]
  const opening = action.endsWith('_open')
  const already = opening ? bool(data.already_running) : bool(data.already_stopped)
  if (language === 'zh') {
    if (already) return `${label} 已经处于${opening ? '打开' : '关闭'}状态。`
    return `${label} 已经${opening ? '打开' : '关闭'}并通过后台验证。`
  }
  if (already) return `${label} is already ${opening ? 'open' : 'closed'}.`
  return `${label} is now ${opening ? 'open' : 'closed'} and verified.`
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function speakWhenIdle(
  text: string,
  language: VoiceLanguage,
  lease: VisitLease | null,
): Promise<void> {
  const deadline = performance.now() + NOTIFICATION_IDLE_TIMEOUT_MS
  while (performance.now() < deadline) {
    if (lease?.signal.aborted) return
    if (lease && !visitLeaseRegistry.isCurrent(lease)) return
    const runtime = realtimeAgent.status()
    if (!runtime.outputActive && !runtime.responseActive && !runtime.speechDetected) {
      await voiceOutputManager.speak(text, language, {
        lease,
        signal: lease?.signal,
        allowLocalFallback: false,
        purpose: 'background_task_verified',
        replyMode: 'exact_operational',
        expectUserResponse: false,
      }).catch((error) => {
        if (error instanceof Error && error.name === 'AbortError') return
        console.error('[BackgroundTask] completion-output-failed', {
          message: error instanceof Error ? error.message : String(error),
        })
      })
      return
    }
    await wait(NOTIFICATION_IDLE_POLL_MS)
  }
  console.info('[BackgroundTask] completion-kept-visual-only-because-conversation-busy', {
    textLength: text.length,
  })
}

async function fetchTask(taskId: string): Promise<LegacyTaskSession> {
  const response = await fetch(`${API_BASE_URL}/agent/tasks/${encodeURIComponent(taskId)}`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Background task status failed: ${response.status} ${await response.text()}`)
  }
  return await response.json() as LegacyTaskSession
}

function subscribe(
  taskId: string,
  action: ManagedBackgroundAction,
  language: VoiceLanguage,
  lease: VisitLease | null,
): void {
  const source = new EventSource(`${API_BASE_URL}/agent/tasks/${encodeURIComponent(taskId)}/events`)
  let settled = false
  const close = () => {
    source.close()
    lease?.signal.removeEventListener('abort', close)
  }
  const finish = async (eventType: string) => {
    if (settled) return
    settled = true
    close()
    try {
      const task = await fetchTask(taskId)
      const text = completionText(action, task, language)
      const detail = { taskId, action, eventType, task, text }
      window.dispatchEvent(new CustomEvent('smartoffice:background-task-terminal', { detail }))
      console.info('[BackgroundTask] terminal', {
        taskId,
        action,
        eventType,
        status: task.status,
      })
      await speakWhenIdle(text, language, lease)
    } catch (error) {
      console.error('[BackgroundTask] terminal-state-fetch-failed', {
        taskId,
        action,
        message: error instanceof Error ? error.message : String(error),
      })
    }
  }
  source.addEventListener('completed', () => void finish('completed'))
  source.addEventListener('error', () => {
    if (source.readyState === EventSource.CLOSED) void finish('error')
  })
  source.addEventListener('cancelled', () => void finish('cancelled'))
  source.addEventListener('verification_result', (event) => {
    window.dispatchEvent(new CustomEvent('smartoffice:background-task-verification', {
      detail: { taskId, action, eventData: (event as MessageEvent<string>).data },
    }))
  })
  lease?.signal.addEventListener('abort', close, { once: true })
}

export async function startManagedBackgroundAction(
  action: ManagedBackgroundAction,
  request: StartRequest,
): Promise<BackgroundTaskAcceptance> {
  const response = await fetch(`${API_BASE_URL}/agent/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({
      text: canonicalCommand(action),
      execute: true,
      conversation_id: request.conversationId,
      visit_id: request.visitId,
      actor_type: request.actor,
    }),
    signal: request.lease?.signal,
  })
  if (!response.ok) {
    throw new Error(`Background task creation failed: ${response.status} ${await response.text()}`)
  }
  const task = await response.json() as LegacyTaskSession
  const taskId = String(task.task_id ?? '').trim()
  if (!taskId) throw new Error('The Backend returned no background task id.')
  subscribe(taskId, action, request.language, request.lease)
  window.dispatchEvent(new CustomEvent('smartoffice:background-task-accepted', {
    detail: { taskId, action, task },
  }))
  return {
    taskId,
    action,
    acceptedText: acceptedText(action, request.language),
  }
}
