import { useEffect, useState } from 'react'
import './ResultCenterCompatibilityTools.css'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

type RecordingRecord = {
  filename: string
  audio_path: string
  artifact_url: string
  conversation_id: string
  size_bytes: number
  uploaded_at: string
}

function formatDate(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN')
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

async function downloadResponse(response: Response, fallbackName: string): Promise<void> {
  if (!response.ok) throw new Error((await response.text()) || `下载失败：${response.status}`)
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = disposition.match(/filename="?([^";]+)"?/i)
  const filename = match?.[1] || fallbackName
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

function ProtectedAudio({ record }: { record: RecordingRecord }) {
  const [source, setSource] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let objectUrl = ''
    void fetch(`${API_BASE_URL}${record.artifact_url}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`音频读取失败：${response.status}`)
        return await response.blob()
      })
      .then((blob) => {
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(blob)
        setSource(objectUrl)
      })
      .catch((value) => {
        if (!controller.signal.aborted) {
          setError(value instanceof Error ? value.message : String(value))
        }
      })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [record.artifact_url])

  if (error) return <span className="protected-audio-error">{error}</span>
  if (!source) return <span className="protected-audio-loading">正在准备安全播放…</span>
  return (
    <audio
      controls
      preload="metadata"
      src={source}
      data-protected-ready="true"
      data-recording-filename={record.filename}
    />
  )
}

export default function ResultCenterCompatibilityTools() {
  const [recordingsOpen, setRecordingsOpen] = useState(false)
  const [recordings, setRecordings] = useState<RecordingRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function loadRecordings(): Promise<void> {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/result-center/recordings?limit=500`)
      if (!response.ok) throw new Error((await response.text()) || `读取录音失败：${response.status}`)
      const payload = await response.json() as { recordings?: RecordingRecord[] }
      setRecordings(payload.recordings ?? [])
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      setLoading(false)
    }
  }

  function showProfiles(): void {
    setRecordingsOpen(false)
    document.querySelector('.profile-center-layout')?.scrollIntoView({ behavior: 'smooth' })
  }

  function showSummaries(): void {
    setRecordingsOpen(false)
    const target = document.querySelector('.profile-summary-list')
      ?? document.querySelector('.profile-section:last-child')
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function showRecordings(): void {
    setRecordingsOpen(true)
    void loadRecordings()
  }

  async function exportCsv(): Promise<void> {
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/result-center/contacts.csv`)
      await downloadResponse(response, 'smart_office_contacts.csv')
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    }
  }

  async function downloadRecording(record: RecordingRecord): Promise<void> {
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}${record.artifact_url}`)
      await downloadResponse(response, record.filename)
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    }
  }

  async function openOutputDirectory(): Promise<void> {
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/result-center/open-output-directory`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error((await response.text()) || `打开目录失败：${response.status}`)
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    }
  }

  useEffect(() => {
    const refresh = () => {
      if (recordingsOpen) void loadRecordings()
      window.dispatchEvent(new CustomEvent('smartoffice:result-center-refresh'))
    }
    window.addEventListener('smartoffice:result-center-refresh-request', refresh)
    return () => window.removeEventListener('smartoffice:result-center-refresh-request', refresh)
  }, [recordingsOpen])

  return (
    <>
      <nav className="result-center-compat-toolbar" aria-label="结果中心功能">
        <button type="button" onClick={showProfiles}>访客档案</button>
        <button type="button" onClick={showSummaries}>Session 总结</button>
        <button type="button" className={recordingsOpen ? 'active' : ''} onClick={showRecordings}>录音文件</button>
        <button type="button" onClick={() => void exportCsv()}>导出 CSV</button>
        <button type="button" onClick={() => {
          window.dispatchEvent(new CustomEvent('smartoffice:result-center-refresh-request'))
        }}>刷新</button>
      </nav>

      {recordingsOpen ? (
        <aside className="result-center-recordings-drawer" aria-label="录音文件">
          <header>
            <div><span>Protected Recordings</span><strong>录音文件</strong></div>
            <div>
              <button type="button" onClick={() => void openOutputDirectory()}>在资源管理器中打开</button>
              <button type="button" onClick={() => setRecordingsOpen(false)}>关闭</button>
            </div>
          </header>
          {loading ? <p className="recording-tools-empty">正在读取录音…</p> : null}
          {error ? <p className="recording-tools-error">{error}</p> : null}
          {!loading && !recordings.length ? <p className="recording-tools-empty">还没有保存的录音。</p> : null}
          <div className="result-center-recording-list">
            {recordings.map((record) => (
              <article key={record.filename}>
                <div>
                  <strong>{record.filename}</strong>
                  <small>{formatDate(record.uploaded_at)} · {formatBytes(record.size_bytes)}</small>
                  <small>{record.audio_path}</small>
                </div>
                <ProtectedAudio record={record} />
                <button type="button" onClick={() => void downloadRecording(record)}>下载</button>
              </article>
            ))}
          </div>
        </aside>
      ) : null}
    </>
  )
}
