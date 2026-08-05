export type VoiceInterruptionDiagnostic = {
  timestamp: string
  event: string
  detail: Record<string, unknown>
}

declare global {
  interface Window {
    __SMART_OFFICE_VOICE_INTERRUPTION_DIAGNOSTICS__?: VoiceInterruptionDiagnostic[]
    __SMART_OFFICE_EXPORT_VOICE_INTERRUPTION_DIAGNOSTICS__?: () => string
  }
}

const MAX_ENTRIES = 240

function safeDetail(detail: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(detail)) {
    if (typeof value === 'string') result[key] = value.slice(0, 500)
    else if (Array.isArray(value)) result[key] = value.slice(0, 40)
    else result[key] = value
  }
  return result
}

export function recordVoiceInterruptionDiagnostic(
  event: string,
  detail: Record<string, unknown> = {},
): VoiceInterruptionDiagnostic {
  const entry: VoiceInterruptionDiagnostic = {
    timestamp: new Date().toISOString(),
    event,
    detail: safeDetail(detail),
  }
  const entries = window.__SMART_OFFICE_VOICE_INTERRUPTION_DIAGNOSTICS__ ?? []
  entries.push(entry)
  if (entries.length > MAX_ENTRIES) entries.splice(0, entries.length - MAX_ENTRIES)
  window.__SMART_OFFICE_VOICE_INTERRUPTION_DIAGNOSTICS__ = entries
  window.__SMART_OFFICE_EXPORT_VOICE_INTERRUPTION_DIAGNOSTICS__ = () =>
    JSON.stringify(window.__SMART_OFFICE_VOICE_INTERRUPTION_DIAGNOSTICS__ ?? [], null, 2)
  console.info('[VoiceInterruptionDiagnostic]', entry)
  return entry
}
