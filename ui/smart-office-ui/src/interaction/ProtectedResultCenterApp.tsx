import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { INTERACTION_PANEL_CLOSE_MESSAGE } from '../display/multiScreenWindowManager'
import ResultCenterCompatibilityTools from './ResultCenterCompatibilityTools'
import VisitorExperienceApp from './VisitorExperienceApp'
import './ProtectedResultCenterApp.css'
import './ResultCenterScrollFix.css'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'

type LoginResponse = {
  ok?: boolean
  access_token?: string
  expires_in_seconds?: number
  visit_id?: string
  panel_instance_id?: string
}

type StatusResponse = {
  ok?: boolean
  configured?: boolean
}

function query(name: string): string {
  return new URLSearchParams(window.location.search).get(name)?.trim() ?? ''
}

function closeProtectedPanel(): void {
  if (window.parent !== window) {
    window.parent.postMessage(
      { type: INTERACTION_PANEL_CLOSE_MESSAGE },
      window.location.origin,
    )
    return
  }
  window.close()
}

async function responseMessage(response: Response): Promise<string> {
  const text = await response.text().catch(() => '')
  if (!text) return `请求失败：${response.status}`
  try {
    const payload = JSON.parse(text) as { detail?: string }
    return String(payload.detail || text)
  } catch {
    return text
  }
}

function shouldAuthorize(url: URL): boolean {
  const apiOrigin = new URL(API_BASE_URL).origin
  return url.origin === apiOrigin && (
    url.pathname.startsWith('/api/result-center/')
    || url.pathname.startsWith('/api/human-recordings/artifacts/')
  )
}

function authorizedRequest(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  token: string,
  visitId: string,
  panelInstanceId: string,
): [RequestInfo | URL, RequestInit | undefined] {
  const sourceUrl = input instanceof Request ? input.url : String(input)
  const url = new URL(sourceUrl, window.location.href)
  if (!shouldAuthorize(url)) return [input, init]

  const headers = new Headers(input instanceof Request ? input.headers : undefined)
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  headers.set('Authorization', `Bearer ${token}`)
  headers.set('X-SmartOffice-Visit-Id', visitId)
  headers.set('X-SmartOffice-Panel-Instance-Id', panelInstanceId)

  if (input instanceof Request) {
    return [new Request(input, { ...init, headers }), undefined]
  }
  return [input, { ...init, headers }]
}

export default function ProtectedResultCenterApp() {
  const visitId = useMemo(() => query('visit_id'), [])
  const panelInstanceId = useMemo(() => {
    const configured = query('panel_instance_id')
    if (configured) return configured
    const generated = crypto.randomUUID()
    const url = new URL(window.location.href)
    url.searchParams.set('panel_instance_id', generated)
    window.history.replaceState(null, '', url)
    return generated
  }, [])
  const [token, setToken] = useState('')
  const [password, setPassword] = useState('')
  const [checking, setChecking] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [configured, setConfigured] = useState(true)
  const [error, setError] = useState('')
  const [expiresIn, setExpiresIn] = useState(0)
  const [authorizedFetchReady, setAuthorizedFetchReady] = useState(false)

  useEffect(() => {
    document.documentElement.dataset.resultCenterAuthenticated = 'false'
    let disposed = false
    const params = new URLSearchParams({ panel_instance_id: panelInstanceId })
    if (visitId) params.set('visit_id', visitId)
    void fetch(`${API_BASE_URL}/api/result-center/admin/status?${params}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseMessage(response))
        return await response.json() as StatusResponse
      })
      .then((status) => {
        if (!disposed) setConfigured(status.configured !== false)
      })
      .catch((value) => {
        if (!disposed) setError(value instanceof Error ? value.message : String(value))
      })
      .finally(() => {
        if (!disposed) setChecking(false)
      })
    return () => { disposed = true }
  }, [panelInstanceId, visitId])

  useEffect(() => {
    if (!token || !visitId) {
      document.documentElement.dataset.resultCenterAuthenticated = 'false'
      setAuthorizedFetchReady(false)
      return
    }
    const originalFetch = window.fetch.bind(window)
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const [nextInput, nextInit] = authorizedRequest(
        input,
        init,
        token,
        visitId,
        panelInstanceId,
      )
      return originalFetch(nextInput, nextInit)
    }
    document.documentElement.dataset.resultCenterAuthenticated = 'true'
    setAuthorizedFetchReady(true)

    const expiryTimer = window.setTimeout(() => {
      setToken('')
      setError('管理员会话已过期，请重新输入密码。')
    }, Math.max(1, expiresIn) * 1000)

    return () => {
      window.clearTimeout(expiryTimer)
      document.documentElement.dataset.resultCenterAuthenticated = 'false'
      setAuthorizedFetchReady(false)
      window.fetch = originalFetch
      const params = new URLSearchParams({
        access_token: token,
        visit_id: visitId,
        panel_instance_id: panelInstanceId,
      })
      void originalFetch(`${API_BASE_URL}/api/result-center/admin/logout?${params}`, {
        method: 'POST',
        keepalive: true,
      }).catch(() => undefined)
    }
  }, [expiresIn, panelInstanceId, token, visitId])

  async function login(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (submitting || !password || !visitId) return
    setSubmitting(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/result-center/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          password,
          visit_id: visitId,
          panel_instance_id: panelInstanceId,
        }),
      })
      if (!response.ok) throw new Error(await responseMessage(response))
      const payload = await response.json() as LoginResponse
      const accessToken = String(payload.access_token ?? '').trim()
      if (!accessToken) throw new Error('Backend 未返回管理员访问令牌。')
      setExpiresIn(Math.max(60, Number(payload.expires_in_seconds) || 600))
      setPassword('')
      setToken(accessToken)
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      setSubmitting(false)
    }
  }

  if (token && authorizedFetchReady) {
    return (
      <>
        <ResultCenterCompatibilityTools />
        <VisitorExperienceApp />
      </>
    )
  }

  return (
    <main className="protected-result-center">
      <section className="protected-result-card">
        <header>
          <span>Smart Office · Protected Results</span>
          <h1>管理员验证</h1>
          <p>结果中心包含访客联系方式、预约信息、Session 对话总结和录音文件。</p>
        </header>
        {checking ? <div className="protected-status">正在检查管理员配置…</div> : null}
        {!checking && !visitId ? (
          <div className="protected-error">当前页面缺少 Visit 标识，请从 Sara 主界面重新打开结果中心。</div>
        ) : null}
        {!checking && !configured ? (
          <div className="protected-error">Backend 尚未配置管理员密码。</div>
        ) : null}
        {!checking && configured && visitId ? (
          <form onSubmit={(event) => void login(event)}>
            <label>
              <span>管理员密码</span>
              <input
                autoFocus
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            {error ? <div className="protected-error" role="alert">{error}</div> : null}
            <div className="protected-actions">
              <button type="button" className="secondary" onClick={closeProtectedPanel}>取消</button>
              <button type="submit" className="primary" disabled={submitting || !password}>
                {submitting ? '正在验证…' : '确认进入'}
              </button>
            </div>
          </form>
        ) : null}
        <small>每个新访客 Session 都必须重新输入管理员密码。密码不会发送给 GPT Realtime，也不会写入对话总结。</small>
      </section>
    </main>
  )
}
