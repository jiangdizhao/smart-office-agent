import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { INTERACTION_PANEL_CLOSE_MESSAGE } from '../display/multiScreenWindowManager'
import InteractionApp from './InteractionApp'
import './ProtectedResultCenterApp.css'

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
  if (url.origin !== apiOrigin) return false
  return (
    url.pathname.startsWith('/api/result-center/')
    || url.pathname.startsWith('/api/human-recordings/artifacts/')
  )
}

function appendContext(
  url: URL,
  token: string,
  visitId: string,
  panelInstanceId: string,
): URL {
  if (!shouldAuthorize(url)) return url
  url.searchParams.set('access_token', token)
  url.searchParams.set('visit_id', visitId)
  url.searchParams.set('panel_instance_id', panelInstanceId)
  return url
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

function rewriteProtectedResources(
  token: string,
  visitId: string,
  panelInstanceId: string,
): void {
  const rewrite = (element: HTMLAnchorElement | HTMLAudioElement | HTMLSourceElement) => {
    const attribute = element instanceof HTMLAnchorElement ? 'href' : 'src'
    const current = element.getAttribute(attribute)
    if (!current) return
    const url = new URL(current, window.location.href)
    if (!shouldAuthorize(url)) return
    const next = appendContext(url, token, visitId, panelInstanceId).toString()
    if (current !== next) element.setAttribute(attribute, next)
  }
  document
    .querySelectorAll<HTMLAnchorElement | HTMLAudioElement | HTMLSourceElement>('a[href], audio[src], source[src]')
    .forEach(rewrite)
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
  const [authorizedFetchReady, setAuthorizedFetchReady] = useState(false)
  const [password, setPassword] = useState('')
  const [checking, setChecking] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [configured, setConfigured] = useState(true)
  const [error, setError] = useState('')
  const [expiresIn, setExpiresIn] = useState(0)

  const statusUrl = useMemo(() => {
    const url = new URL(`${API_BASE_URL}/api/result-center/admin/status`)
    if (visitId) url.searchParams.set('visit_id', visitId)
    url.searchParams.set('panel_instance_id', panelInstanceId)
    return url.toString()
  }, [panelInstanceId, visitId])

  useEffect(() => {
    document.documentElement.dataset.resultCenterAuthenticated = 'false'
    let disposed = false
    void fetch(statusUrl)
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
  }, [statusUrl])

  useEffect(() => {
    if (!token || !visitId) {
      document.documentElement.dataset.resultCenterAuthenticated = 'false'
      setAuthorizedFetchReady(false)
      return
    }
    const originalFetch = window.fetch.bind(window)
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const [authorizedInput, authorizedInit] = authorizedRequest(
        input,
        init,
        token,
        visitId,
        panelInstanceId,
      )
      return originalFetch(authorizedInput, authorizedInit)
    }
    document.documentElement.dataset.resultCenterAuthenticated = 'true'
    setAuthorizedFetchReady(true)

    const rewrite = () => rewriteProtectedResources(token, visitId, panelInstanceId)
    rewrite()
    const observer = new MutationObserver(rewrite)
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['href', 'src'],
    })
    const expiryTimer = window.setTimeout(() => {
      setToken('')
      setError('管理员会话已过期，请重新输入密码。')
    }, Math.max(1, expiresIn) * 1000)

    return () => {
      window.clearTimeout(expiryTimer)
      observer.disconnect()
      document.documentElement.dataset.resultCenterAuthenticated = 'false'
      setAuthorizedFetchReady(false)
      window.fetch = originalFetch
      const logout = new URL(`${API_BASE_URL}/api/result-center/admin/logout`)
      logout.searchParams.set('access_token', token)
      logout.searchParams.set('visit_id', visitId)
      logout.searchParams.set('panel_instance_id', panelInstanceId)
      void originalFetch(logout, { method: 'POST', keepalive: true }).catch(() => undefined)
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
      if (
        payload.visit_id !== visitId
        || payload.panel_instance_id !== panelInstanceId
      ) {
        throw new Error('Backend 返回的管理员会话上下文不匹配。')
      }
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

  if (token && authorizedFetchReady) return <InteractionApp />

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
          <p>结果中心包含访客登记信息和录音文件。每个新访客 Session 都必须重新输入管理员密码。</p>
          <p className="result-admin-gate-warning">请勿通过语音说出密码。密码不会发送给 GPT Realtime。</p>

          {!visitId ? (
            <div className="result-admin-gate-error" role="alert">
              当前结果中心没有绑定有效的访客 Session。请关闭后从 Sara 主界面重新打开。
            </div>
          ) : checking || (token && !authorizedFetchReady) ? (
            <div className="result-admin-gate-status">正在检查管理员配置…</div>
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
          {expiresIn > 0 ? <small>本面板的管理员会话将在 {Math.ceil(expiresIn / 60)} 分钟内自动失效；关闭面板或进入新访客 Session 会立即失效。</small> : null}
        </div>
      </section>
    </main>
  )
}
