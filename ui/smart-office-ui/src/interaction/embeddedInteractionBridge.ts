import {
  INTERACTION_PANEL_CLOSE_MESSAGE,
  INTERACTION_PANEL_RESULT_MESSAGE,
  type InteractionPanelCommandResult,
  type InteractionWindowKind,
} from '../display/multiScreenWindowManager'

const parameters = new URLSearchParams(window.location.search)
const embedded =
  window.parent !== window &&
  window.location.pathname.startsWith('/interaction/') &&
  parameters.get('embedded') === '1'

if (embedded) {
  const panelInstanceId = parameters.get('panel_instance_id')?.trim() ?? ''
  const visitId = parameters.get('visit_id')?.trim() || null

  const notifyParent = () => {
    window.parent.postMessage({ type: INTERACTION_PANEL_CLOSE_MESSAGE }, window.location.origin)
  }

  const notifyResult = (
    target: InteractionWindowKind,
    action: 'submit_booking' | 'submit_contact',
    ok: boolean,
    message: string,
    data: Record<string, unknown> = {},
  ) => {
    if (!panelInstanceId) return
    const result: InteractionPanelCommandResult = {
      commandId: crypto.randomUUID(),
      panelInstanceId,
      visitId,
      target,
      action,
      ok,
      status: ok ? 'completed' : 'failed',
      message,
      data: { ...data, verified: ok },
    }
    window.parent.postMessage(
      { type: INTERACTION_PANEL_RESULT_MESSAGE, result },
      window.location.origin,
    )
  }

  const originalFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url
    const method = String(init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
    const contactSubmission = method === 'POST' && /\/api\/contact-records(?:\?|$)/.test(url)
    const bookingSubmission = method === 'POST' && /\/api\/visitor-experience\/meeting-bookings(?:\?|$)/.test(url)
    if (!contactSubmission && !bookingSubmission) return await originalFetch(input, init)

    const target: InteractionWindowKind = contactSubmission ? 'contact' : 'meeting'
    const action = contactSubmission ? 'submit_contact' : 'submit_booking'
    try {
      const response = await originalFetch(input, init)
      let payload: Record<string, unknown> = {}
      try {
        const parsed = await response.clone().json() as Record<string, unknown>
        payload = parsed && typeof parsed === 'object' ? parsed : {}
      } catch {
        payload = {}
      }
      notifyResult(
        target,
        action,
        response.ok,
        response.ok
          ? contactSubmission ? 'Visitor contact information was verified and saved.' : 'Meeting booking was verified and saved.'
          : `Submission failed with HTTP ${response.status}.`,
        payload,
      )
      return response
    } catch (error) {
      notifyResult(
        target,
        action,
        false,
        error instanceof Error ? error.message : String(error),
      )
      throw error
    }
  }

  try {
    Object.defineProperty(window, 'close', {
      configurable: true,
      value: notifyParent,
    })
  } catch {
    // The parent panel still exposes its own close button if the browser refuses
    // to replace window.close inside the same-origin iframe.
  }

  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') notifyParent()
  })
}
