import { voiceOutputManager } from '../voice/voiceOutputManager'

type IntroGate = {
  requestId: string
  createdAt: number
  promise: Promise<void>
  release: () => void
  consumed: boolean
}

type IntroEventDetail = {
  requestId?: string
}

const GATE_TIMEOUT_MS = 7_000
const INSTALL_KEY = '__SMART_OFFICE_INTRO_VOICE_GATE_INSTALLED__'
let pendingGate: IntroGate | null = null

function releaseGate(reason: string, requestId?: string): void {
  const gate = pendingGate
  if (!gate) return
  if (requestId && gate.requestId !== requestId) return
  pendingGate = null
  console.info('[VirtualHostVideo] intro voice gate released', {
    requestId: gate.requestId,
    reason,
    waitedMs: Math.round(performance.now() - gate.createdAt),
  })
  gate.release()
}

function createGate(requestId: string): IntroGate {
  let release: () => void = () => undefined
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  const gate: IntroGate = {
    requestId,
    createdAt: performance.now(),
    promise,
    release,
    consumed: false,
  }
  window.setTimeout(() => releaseGate('timeout', requestId), GATE_TIMEOUT_MS)
  return gate
}

function installIntroVoiceGate(): void {
  const globalWindow = window as unknown as Window & Record<string, unknown>
  if (globalWindow[INSTALL_KEY]) return
  globalWindow[INSTALL_KEY] = true

  window.addEventListener('smartoffice:host-intro-start', (event) => {
    const detail = event instanceof CustomEvent
      ? event.detail as IntroEventDetail & Record<string, unknown>
      : {}
    const requestId = String(detail.requestId ?? crypto.randomUUID())
    detail.requestId = requestId
    releaseGate('superseded')
    pendingGate = createGate(requestId)
  })

  window.addEventListener('smartoffice:host-intro-playback-started', (event) => {
    const detail = event instanceof CustomEvent ? event.detail as IntroEventDetail : {}
    releaseGate('video-started', detail.requestId)
  })
  window.addEventListener('smartoffice:host-intro-playback-failed', (event) => {
    const detail = event instanceof CustomEvent ? event.detail as IntroEventDetail : {}
    releaseGate('video-failed', detail.requestId)
  })
  window.addEventListener('smartoffice:host-intro-cancel', () => releaseGate('intro-cancelled'))
  window.addEventListener('smartoffice:visit-revoked', () => releaseGate('visit-revoked'))

  const originalSpeak = voiceOutputManager.speak.bind(voiceOutputManager)
  voiceOutputManager.speak = async (...args: Parameters<typeof originalSpeak>): Promise<void> => {
    const gate = pendingGate
    if (gate && !gate.consumed) {
      gate.consumed = true
      await gate.promise
    }
    await originalSpeak(...args)
  }
}

installIntroVoiceGate()
