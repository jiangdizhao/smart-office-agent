import { realtimeOfficeInterpreter } from '../voice/realtimeOfficeInterpreter'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import { visitLeaseRegistry } from '../vision/visitLeaseRegistry'

const DELEGATE_TTL_MS = 8_000

type PendingDelegate = {
  originalText: string
  delegateText: string
  visitId: string
  expiresAt: number
}

let pending: PendingDelegate | null = null
let installed = false

function normalise(value: string): string {
  return value.normalize('NFKC').replace(/\s+/g, ' ').trim().toLocaleLowerCase()
}

export function queueSalesOfficeDelegate(input: {
  originalText: string
  delegateText: string
  visitId: string
}): void {
  pending = {
    originalText: input.originalText.trim(),
    delegateText: input.delegateText.trim(),
    visitId: input.visitId.trim(),
    expiresAt: Date.now() + DELEGATE_TTL_MS,
  }
  console.info('[SalesRuntime] office-delegate-queued', {
    visitId: pending.visitId,
    delegateText: pending.delegateText,
  })
}

function takeDelegate(text: string): PendingDelegate | null {
  const candidate = pending
  pending = null
  if (!candidate || candidate.expiresAt <= Date.now()) return null
  const lease = visitLeaseRegistry.current()
  if (!lease || lease.visitId !== candidate.visitId || lease.signal.aborted) return null
  if (normalise(text) !== normalise(candidate.originalText)) return null
  return candidate
}

function installSalesOfficeDelegate(): void {
  if (installed) return
  installed = true
  const originalInterpret = realtimeOfficeInterpreter.interpret.bind(realtimeOfficeInterpreter)
  realtimeOfficeInterpreter.interpret = async (
    text: string,
    language: VoiceLanguage,
  ) => {
    const delegate = takeDelegate(text)
    if (!delegate) return await originalInterpret(text, language)
    console.info('[SalesRuntime] office-delegate-consumed', {
      visitId: delegate.visitId,
      originalText: delegate.originalText,
      delegateText: delegate.delegateText,
    })
    return await originalInterpret(delegate.delegateText, language)
  }

  window.addEventListener('smartoffice:visit-revoked', () => {
    pending = null
  })
  window.addEventListener('smartoffice:visit-activated', (event: Event) => {
    const detail = event instanceof CustomEvent ? event.detail : null
    if (detail?.replacedVisitId) pending = null
  })
}

installSalesOfficeDelegate()
