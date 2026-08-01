import { INTERACTION_PANEL_CLOSE_MESSAGE } from '../display/multiScreenWindowManager'

const embedded =
  window.parent !== window &&
  window.location.pathname.startsWith('/interaction/') &&
  new URLSearchParams(window.location.search).get('embedded') === '1'

if (embedded) {
  const notifyParent = () => {
    window.parent.postMessage({ type: INTERACTION_PANEL_CLOSE_MESSAGE }, window.location.origin)
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
