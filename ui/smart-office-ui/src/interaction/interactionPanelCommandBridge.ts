import {
  INTERACTION_PANEL_COMMAND_MESSAGE,
  INTERACTION_PANEL_READY_MESSAGE,
  INTERACTION_PANEL_RESULT_MESSAGE,
  type InteractionPanelCommand,
  type InteractionPanelCommandResult,
  type InteractionWindowKind,
} from '../display/multiScreenWindowManager'

const embedded =
  window.parent !== window
  && window.location.pathname.startsWith('/interaction/')
  && new URLSearchParams(window.location.search).get('embedded') === '1'

const params = new URLSearchParams(window.location.search)
const panelInstanceId = params.get('panel_instance_id')?.trim() ?? ''
const visitId = params.get('visit_id')?.trim() || null
const conversationId = params.get('conversation_id')?.trim() ?? ''
const language = params.get('lang') === 'en' ? 'en' : 'zh'
const routeMatch = window.location.pathname.match(/\/interaction\/(contact|recording|transcript|results)/)
const target = (routeMatch?.[1] ?? 'contact') as InteractionWindowKind
const processed = new Set<string>()
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

function pageText(): string {
  return document.body?.innerText ?? ''
}

function controls(): Array<HTMLButtonElement | HTMLAnchorElement> {
  return Array.from(document.querySelectorAll<HTMLButtonElement | HTMLAnchorElement>('button, a'))
}

function controlByText(pattern: RegExp): HTMLButtonElement | HTMLAnchorElement | null {
  return controls().find((control) => pattern.test(control.textContent?.trim() ?? '')) ?? null
}

function clickControl(pattern: RegExp): boolean {
  const control = controlByText(pattern)
  if (!control) return false
  control.click()
  return true
}

function waitFor(
  predicate: () => boolean,
  timeoutMs: number,
  intervalMs = 120,
): Promise<boolean> {
  const deadline = performance.now() + timeoutMs
  return new Promise((resolve) => {
    const inspect = () => {
      if (predicate()) {
        resolve(true)
        return
      }
      if (performance.now() >= deadline) {
        resolve(false)
        return
      }
      window.setTimeout(inspect, intervalMs)
    }
    inspect()
  })
}

function recordingActive(): boolean {
  return Boolean(controlByText(/^停止并保存$/)) || pageText().includes('正在录音')
}

function recordingSaved(): boolean {
  return pageText().includes('录音已保存') || Boolean(controlByText(/^已保存$/))
}

async function startRecording(): Promise<Record<string, unknown>> {
  if (recordingActive()) return { already_active: true }
  if (!clickControl(/^开始录音$/)) {
    throw new Error('录音面板尚未准备好开始录音。')
  }
  const started = await waitFor(recordingActive, 15_000)
  if (!started) throw new Error('浏览器没有确认录音已经开始。')
  return { recording_active: true }
}

async function stopAndSaveRecording(): Promise<Record<string, unknown>> {
  if (recordingSaved()) return { already_saved: true }
  if (!recordingActive()) {
    throw new Error('当前没有正在进行的录音。')
  }
  if (!clickControl(/^停止并保存$/)) {
    throw new Error('录音面板没有提供停止并保存操作。')
  }
  const saved = await waitFor(recordingSaved, 45_000, 200)
  if (!saved) throw new Error('录音停止后未能在限定时间内完成保存。')
  return { recording_active: false, recording_saved: true }
}

async function summarizeRecording(
  stopFirst: boolean,
): Promise<Record<string, unknown>> {
  if (stopFirst && recordingActive()) await stopAndSaveRecording()
  const response = await fetch(
    `${API_BASE_URL}/api/human-recordings/${encodeURIComponent(conversationId)}/summary`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ language }),
    },
  )
  if (!response.ok) {
    throw new Error((await response.text()) || `录音总结失败：${response.status}`)
  }
  const payload = await response.json() as Record<string, unknown>
  return {
    recording_summary_created: Boolean(payload.ok),
    artifact_url: payload.artifact_url ?? null,
    document_path: payload.document_path ?? null,
    opened_in_word: Boolean(payload.opened_in_word),
    spoken_text: payload.spoken_text ?? null,
  }
}

function resultCenterAuthenticated(): boolean {
  return document.documentElement.dataset.resultCenterAuthenticated === 'true'
}

async function execute(command: InteractionPanelCommand): Promise<Record<string, unknown>> {
  if (command.action === 'close') {
    window.close()
    return { closed: true }
  }

  if (command.target === 'contact') return { opened: true }

  if (command.target === 'recording') {
    if (command.action === 'start') return await startRecording()
    if (command.action === 'stop_save') return await stopAndSaveRecording()
    if (command.action === 'summarize') return await summarizeRecording(true)
    if (command.action === 'stop_save_summarize') {
      if (recordingActive()) await stopAndSaveRecording()
      return await summarizeRecording(false)
    }
    if (command.action === 'download') {
      const link = document.querySelector<HTMLAnchorElement>('a[download]')
      if (!link) throw new Error('当前没有可下载的已保存录音。')
      link.click()
      return { download_started: true }
    }
    return { opened: true }
  }

  if (command.target === 'transcript') {
    if (command.action === 'refresh') {
      return { refreshed: true, note: 'The transcript already refreshes automatically.' }
    }
    return { opened: true }
  }

  if (!resultCenterAuthenticated()) {
    const error = new Error('管理员验证尚未完成。')
    error.name = 'AuthRequiredError'
    throw error
  }

  if (command.action === 'show_contacts') {
    if (!clickControl(/^登记信息/)) throw new Error('未找到登记信息标签。')
    return { selected_tab: 'contacts' }
  }
  if (command.action === 'show_recordings') {
    if (!clickControl(/^录音文件/)) throw new Error('未找到录音文件标签。')
    return { selected_tab: 'recordings' }
  }
  if (command.action === 'show_transcript') {
    if (!clickControl(/^当前对话$/)) throw new Error('未找到当前对话标签。')
    return { selected_tab: 'transcript' }
  }
  if (command.action === 'refresh') {
    if (!clickControl(/^刷新$/)) throw new Error('当前结果标签没有刷新按钮。')
    return { refreshed: true }
  }
  if (command.action === 'export_csv') {
    if (!clickControl(/^导出 CSV$/i)) throw new Error('未找到联系人 CSV 导出操作。')
    return { export_started: true }
  }
  if (command.action === 'open_directory') {
    if (!clickControl(/在资源管理器中打开/)) throw new Error('未找到打开录音目录操作。')
    return { explorer_open_requested: true }
  }
  if (command.action === 'play_latest_recording') {
    const audio = document.querySelector<HTMLAudioElement>('audio')
    if (!audio) throw new Error('结果中心没有可播放的录音。')
    await audio.play()
    return { playback_started: true }
  }
  return { opened: true }
}

function resultFor(
  command: InteractionPanelCommand,
  ok: boolean,
  status: InteractionPanelCommandResult['status'],
  message: string,
  data?: Record<string, unknown>,
): InteractionPanelCommandResult {
  return {
    commandId: command.commandId,
    panelInstanceId,
    visitId,
    target: command.target,
    action: command.action,
    ok,
    status,
    message,
    data,
  }
}

async function handleCommand(command: InteractionPanelCommand): Promise<void> {
  if (processed.has(command.commandId)) return
  processed.add(command.commandId)
  let result: InteractionPanelCommandResult
  if (
    command.panelInstanceId !== panelInstanceId
    || command.target !== target
    || String(command.visitId ?? '') !== String(visitId ?? '')
  ) {
    result = resultFor(command, false, 'stale', 'The command belongs to a stale panel or Visit.')
  } else {
    try {
      const data = await execute(command)
      result = resultFor(command, true, 'completed', 'Interaction panel command completed.', data)
    } catch (error) {
      const authRequired = error instanceof Error && error.name === 'AuthRequiredError'
      result = resultFor(
        command,
        false,
        authRequired ? 'auth_required' : 'failed',
        error instanceof Error ? error.message : String(error),
      )
    }
  }
  window.parent.postMessage(
    { type: INTERACTION_PANEL_RESULT_MESSAGE, result },
    window.location.origin,
  )
}

if (embedded && panelInstanceId && conversationId) {
  window.addEventListener('message', (event: MessageEvent) => {
    if (event.origin !== window.location.origin || event.source !== window.parent) return
    if (event.data?.type !== INTERACTION_PANEL_COMMAND_MESSAGE) return
    const command = event.data?.command as InteractionPanelCommand | undefined
    if (command) void handleCommand(command)
  })

  const announceReady = () => {
    window.parent.postMessage(
      {
        type: INTERACTION_PANEL_READY_MESSAGE,
        panelInstanceId,
        visitId,
        target,
      },
      window.location.origin,
    )
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', announceReady, { once: true })
  } else {
    announceReady()
  }
}
