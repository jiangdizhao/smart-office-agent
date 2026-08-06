import { voiceOutputManager, type AssistantOutputLifecycleDetail } from './voiceOutputManager'

let presentationActive = false
let installed = false

function install(): void {
  if (installed || typeof window === 'undefined') return
  installed = true

  window.addEventListener('smartoffice:presentation-session-state', (event: Event) => {
    const detail = event instanceof CustomEvent ? event.detail : null
    presentationActive = Boolean(detail?.active)
    document.documentElement.dataset.presentationSessionActive = presentationActive ? 'true' : 'false'
  })

  window.addEventListener('smartoffice:assistant-output-started', (event: Event) => {
    if (!presentationActive) return
    const detail = event instanceof CustomEvent
      ? event.detail as AssistantOutputLifecycleDetail
      : null
    const purpose = String(detail?.purpose ?? '')
    if (purpose.startsWith('presentation_')) return
    console.info('[PresentationSession] suppressed competing assistant output', {
      purpose: purpose || 'unknown',
      outputId: detail?.outputId ?? null,
    })
    void voiceOutputManager.stop('presentation-session-exclusive-speech')
  })
}

install()
