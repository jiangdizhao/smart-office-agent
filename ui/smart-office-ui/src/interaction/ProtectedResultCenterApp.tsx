import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { INTERACTION_PANEL_CLOSE_MESSAGE } from '../display/multiScreenWindowManager'
import InteractionApp from './InteractionApp'
import './ProtectedResultCenterApp.css'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const TOKEN_KEY = 'smartoffice_result_center_admin_token'

type LoginResponse = {
  ok?: boolean
  access_token?: string
  expires_in_seconds?: number
}

type StatusResponse = {
  ok?: boolean
  configured?: boolean
  authenticated?: boolean
  expires_in_seconds?: number
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
  if (url.origin !== apiOrigin) return false
  return (
    url.pathname.startsWith('/api/result-center/')
    || url.pathname.startsWith('/api/human-recordings/artifacts/')
  )
}

function authorizedRequest(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  token: string,
): [RequestInfo | URL, RequestInit | undefined] {
  const sourceUrl = input instanceof Request ? input.url : String(input)
  const url = new URL(sourceUrl, window.location.href)
  if (!shouldAuthorize(url)) return [input, init]

  const headers = new Headers(input instanceof Request ? input.headers : undefined)
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  headers.set('Authorization', `Bearer ${token}`)

  if (input instanceof Request) {
    return [new Request(input, { ...init, headers }), undefined]
  }
  return [input, { ...init, headers }]
}

export default function ProtectedResultCenterApp() {
  const [token, setToken] = useState('')
  const [password, setPassword] = useState('')
  const [checking, setChecking] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [configured, setConfigured] = useState(true)
  const [error, setError] = useState('')
  const [expiresIn, setExpiresIn] = useState(0)

  const statusUrl = useMemo(
    () => `${API_BASE_URL}/api/result-center/admin/status`,
    [],
  )

  useEffect(() => {
    let disposed = false
    const existing = sessionStorage.getItem(TOKEN_KEY) ?? ''
    const headers = existing
      ? { Authorization: `Bearer ${existing}` }
      : undefined
    void fetch(statusUrl, { headers })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseMessage(response))
        return await response.json() as StatusResponse
      })
      .then((status) => {
        if (disposed) return
        setConfigured(status.configured !== false)
        if (existing && status.authenticated) {
          setToken(existing)
          setExpiresIn(Number(status.expires_in_seconds ?? 0))
        } else {
          sessionStorage.removeItem(TOKEN_KEY)
        }
      })
      .catch((value) => {
        if (!disposed) setError(value instanceof Error ? value.message : String(value))
      })
      .finally(() => {
        if (!disposed) setChecking(false)
      })
    return () => { disposed = true }
  }, [statusUrl])

  useEffect(() => {
    if (!token) return
    const originalFetch = window.fetch.bind(window)
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const [authorizedInput, authorizedInit] = authorizedRequest(input, init, token)
      return originalFetch(authorizedInput, authorizedInit)
    }

    return () => {
      window.fetch = originalFetch
      sessionStorage.removeItem(TOKEN_KEY)
      void originalFetch(`${API_BASE_URL}/api/result-center/admin/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        keepalive: true,
      }).catch(() => undefined)
    }
  }, [token])

  async function login(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (submitting || !password) return
    setSubmitting(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/result-center/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({ password }),
      })
      if (!response.ok) throw new Error(await responseMessage(response))
      const payload = await response.json() as LoginResponse
      const accessToken = String(payload.access_token ?? '').trim()
      if (!accessToken) throw new Error('Backend 未返回管理员访问令牌。')
      sessionStorage.setItem(TOKEN_KEY, accessToken)
      setPassword('')
      setExpiresIn(Number(payload.expires_in_seconds ?? 0))
      setToken(accessToken)
    } catch (value) {
      setPassword('')
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      setSubmitting(false)
    }
  }

  if (token) return <InteractionApp />

  return (
    <main className="result-admin-gate-shell">
      <section className="result-admin-gate-card" role="dialog" aria-modal="true">
        <header>
          <div className="result-admin-gate-icon" aria-hidden="true">🔒</div>
          <div>
            <span>Administrator Verification</span>
            <h1>管理员验证</h1>
          </div>
          <button
            type="button"
            className="result-admin-gate-close"
            onClick={closeProtectedPanel}
            aria-label="关闭管理员验证"
          >×</button>
        </header>

        <div className="result-admin-gate-body">
          <p>结果中心包含访客登记信息和录音文件。请输入管理员密码后继续。</p>
          <p className="result-admin-gate-warning">请勿通过语音说出密码。密码不会发送给 GPT Realtime。</p>

          {checking ? (
            <div className="result-admin-gate-status">正在检查管理员会话…</div>
          ) : !configured ? (
            <div className="result-admin-gate-error" role="alert">
              Backend 尚未配置管理员密码。请设置 SMART_OFFICE_ADMIN_PASSWORD 或 SMART_OFFICE_ADMIN_PASSWORD_HASH 后重启 Backend。
            </div>
          ) : (
            <form onSubmit={(event) => void login(event)}>
              <label>
                <span>管理员密码</span>
                <input
                  type="password"
                  autoFocus
                  autoComplete="current-password"
                  maxLength={512}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={submitting}
                />
              </label>
              {error ? <div className="result-admin-gate-error" role="alert">{error}</div> : null}
              <div className="result-admin-gate-actions">
                <button type="button" className="secondary" onClick={closeProtectedPanel}>取消</button>
                <button type="submit" className="primary" disabled={submitting || !password}>
                  {submitting ? '正在验证…' : '确认进入'}
                </button>
              </div>
            </form>
          )}
          {expiresIn > 0 ? <small>管理员会话将在 {Math.ceil(expiresIn / 60)} 分钟内自动失效。</small> : null}
        </div>
      </section>
    </main>
  )
}
