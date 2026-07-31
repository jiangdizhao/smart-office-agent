import { realtimeAgent } from './realtimeAgentRuntime'
import { voiceOutputManager } from './voiceOutputManager'

let installed = false

export function installVisitRealtimeLeaseBridge(): void {
  if (installed) return
  installed = true

  window.addEventListener('smartoffice:visit-activated', (event) => {
    const detail = event instanceof CustomEvent ? event.detail : null
    if (!detail?.replacedVisitId) return
    // shutdown() fences the old generation synchronously before its first await.
    // The replacement Visit may establish a fresh connection immediately.
    void realtimeAgent.shutdown().catch(() => undefined)
    void voiceOutputManager.stop().catch(() => undefined)
  })

  window.addEventListener('smartoffice:visit-revoked', () => {
    void realtimeAgent.shutdown().catch(() => undefined)
    void voiceOutputManager.stop().catch(() => undefined)
  })
}

installVisitRealtimeLeaseBridge()
