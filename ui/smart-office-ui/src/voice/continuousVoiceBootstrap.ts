import './realtimeLivenessPatch'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'
import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'

function languageFromText(text: string): VoiceLanguage {
  return /[\u3400-\u9fff]/.test(text) ? 'zh' : 'en'
}

window.addEventListener('smartoffice:visit-activated', () => {
  const lease = visitLeaseRegistry.current()
  if (!lease) return
  void realtimeAgent.prewarm('zh', lease.signal).catch((error) => {
    if (!lease.signal.aborted) {
      console.error('[RealtimeDiagnostics] continuous-prewarm-failed', {
        visitId: lease.visitId,
        message: error instanceof Error ? error.message : String(error),
      })
    }
  })
})

window.addEventListener('smartoffice:host-intro-start', (event: Event) => {
  const lease = visitLeaseRegistry.current()
  if (!lease) return
  const detail = event instanceof CustomEvent ? event.detail : null
  const welcomeText = String(detail?.welcomeText ?? '')
  const language = languageFromText(welcomeText)
  void realtimeAgent.startContinuousCapture(language, lease.signal).catch((error) => {
    if (!lease.signal.aborted) {
      console.error('[RealtimeDiagnostics] continuous-listening-before-welcome-failed', {
        visitId: lease.visitId,
        message: error instanceof Error ? error.message : String(error),
      })
    }
  })
})

window.addEventListener('smartoffice:visit-revoked', () => {
  void realtimeAgent.stopContinuousCapture(true).catch(() => undefined)
})
