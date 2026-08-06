import './presentationLanguageFetchPatch'
import { realtimeAgent } from './realtimeAgentRuntime'

type RealtimeTranscriptionInternals = {
  transcribeVadTurn?: (itemId: string, generation: number) => Promise<void>
}

type SpeechStoppedDetail = {
  itemId?: string | null
}

const PATCH_KEY = '__smartOfficeLatestVadPatchInstalled__'
const runtimeWindow = window as typeof window & Record<string, unknown>

if (!runtimeWindow[PATCH_KEY]) {
  runtimeWindow[PATCH_KEY] = true
  const internals = realtimeAgent as unknown as RealtimeTranscriptionInternals
  const original = internals.transcribeVadTurn?.bind(realtimeAgent)
  let latestStoppedItemId = ''

  window.addEventListener('smartoffice:realtime-vad-speech-stopped', (event: Event) => {
    const detail = event instanceof CustomEvent
      ? (event as CustomEvent<SpeechStoppedDetail>).detail
      : null
    latestStoppedItemId = String(detail?.itemId ?? '').trim()
  })
  window.addEventListener('smartoffice:visit-activated', () => {
    latestStoppedItemId = ''
  })
  window.addEventListener('smartoffice:visit-revoked', () => {
    latestStoppedItemId = ''
  })

  if (original) {
    internals.transcribeVadTurn = async (itemId: string, generation: number) => {
      const normalized = String(itemId ?? '').trim()
      if (normalized && latestStoppedItemId && normalized !== latestStoppedItemId) {
        console.info('[RealtimeDiagnostics] stale-vad-transcription-skipped', {
          itemId: normalized,
          latestItemId: latestStoppedItemId,
          generation,
        })
        return
      }
      await original(normalized, generation)
    }
  }
}
