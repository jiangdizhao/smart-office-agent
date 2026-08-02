const CONTACT_PATH = '/interaction/contact'
const CONSENT_VERSION = 'expo-contact-summary-booking-v2'
const CONSENT_TEXT = '我同意 Smart Office 团队保存以上联系方式、当前服务过程的要点总结及预约信息，用于展会后的产品联系和会议安排。本授权不包含人脸或其他生物识别信息。'

function isContactRoute(): boolean {
  return window.location.pathname.replace(/\/+$/, '') === CONTACT_PATH
}

function updateConsentText(): void {
  const label = document.querySelector<HTMLElement>('.consent-field span')
  if (label && label.textContent !== CONSENT_TEXT) label.textContent = CONSENT_TEXT
}

function installContactRequestPatch(): void {
  if (!isContactRoute()) return
  const originalFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const source = input instanceof Request ? input.url : String(input)
    const url = new URL(source, window.location.href)
    const isCreateContact =
      url.pathname === '/api/contact-records'
      && String(init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase() === 'POST'
    if (!isCreateContact) return await originalFetch(input, init)

    let body = init?.body
    if (typeof body === 'string') {
      try {
        const payload = JSON.parse(body) as Record<string, unknown>
        payload.consent_statement_version = CONSENT_VERSION
        body = JSON.stringify(payload)
      } catch {
        // Backend validation remains authoritative when the request is not JSON.
      }
    }
    return await originalFetch(input, { ...init, body })
  }
}

if (isContactRoute()) {
  installContactRequestPatch()
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', updateConsentText, { once: true })
  } else {
    updateConsentText()
  }
  const observer = new MutationObserver(updateConsentText)
  observer.observe(document.documentElement, { childList: true, subtree: true })
  window.addEventListener('beforeunload', () => observer.disconnect(), { once: true })
}
