import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'

const PATCH_FLAG = '__smartOfficeCaptureCleanupInstalled__'
const LANGUAGE_PATCH_FLAG = '__smartOfficeVerbatimLanguageTranscriptionInstalled__'
const CONNECTION_PATCH_FLAG = '__smartOfficeRealtimeConnectionGuardInstalled__'
const CONNECTION_GUARD_TIMEOUT_MS = 50_000

type GuardedRealtimeAgent = typeof realtimeAgent & {
  [PATCH_FLAG]?: boolean
}

type PatchableRealtimeAgent = {
  [LANGUAGE_PATCH_FLAG]?: boolean
  transcriptionInstructions: () => string
}

type ConnectionGuardedRealtimeAgent = {
  [CONNECTION_PATCH_FLAG]?: boolean
  connectPromise?: Promise<void> | null
  shutdown: () => Promise<void>
}

function timeoutError(message: string): Error {
  const error = new Error(message)
  error.name = 'TimeoutError'
  return error
}

async function withTimeout<T>(
  operation: Promise<T>,
  timeoutMs: number,
  message: string,
): Promise<T> {
  let timer: number | null = null
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = window.setTimeout(() => reject(timeoutError(message)), timeoutMs)
  })
  try {
    return await Promise.race([operation, timeout])
  } finally {
    if (timer !== null) window.clearTimeout(timer)
  }
}

async function resetStalledConnection(agent: ConnectionGuardedRealtimeAgent): Promise<void> {
  // shutdown() closes any partially-created PeerConnection/DataChannel. The
  // connectPromise field is then cleared explicitly so a retry cannot inherit a
  // permanently pending promise left by a stalled browser fetch or WebRTC setup.
  await agent.shutdown().catch(() => undefined)
  agent.connectPromise = null
}

/**
 * Guard the persistent Realtime connection against browser fetch/WebRTC calls
 * that never settle. The underlying runtime already times out the data channel,
 * but the status/session fetches previously had no browser-side deadline. That
 * could leave the UI in the "connecting" state forever and make every retry
 * await the same stale connectPromise.
 */
export function installRealtimeConnectionGuard(): void {
  const guardedAgent = realtimeAgent as unknown as ConnectionGuardedRealtimeAgent
  if (guardedAgent[CONNECTION_PATCH_FLAG]) return

  const originalPrewarm = realtimeAgent.prewarm.bind(realtimeAgent)
  realtimeAgent.prewarm = async (language: VoiceLanguage): Promise<void> => {
    try {
      await withTimeout(
        originalPrewarm(language),
        CONNECTION_GUARD_TIMEOUT_MS,
        'GPT Realtime connection timed out. Check the Backend Realtime status, API key, model, and network, then retry.',
      )
    } catch (error) {
      await resetStalledConnection(guardedAgent)
      throw error
    }
  }

  guardedAgent[CONNECTION_PATCH_FLAG] = true
}

/**
 * Install a single cleanup guard around the shared Realtime capture methods.
 *
 * The normal success path already replaces the physical microphone with the
 * silent WebRTC track. The guard covers exceptional paths such as connection,
 * audio-buffer commit, or transcription failures and guarantees that the real
 * microphone track is stopped before the error reaches the UI.
 */
export function installRealtimeCaptureCleanup(): void {
  const guardedAgent = realtimeAgent as GuardedRealtimeAgent
  if (guardedAgent[PATCH_FLAG]) return

  const originalBeginCapture = realtimeAgent.beginCapture.bind(realtimeAgent)
  const originalEndCapture = realtimeAgent.endCapture.bind(realtimeAgent)

  realtimeAgent.beginCapture = async (language: VoiceLanguage): Promise<void> => {
    try {
      await withTimeout(
        originalBeginCapture(language),
        CONNECTION_GUARD_TIMEOUT_MS,
        'GPT Realtime microphone connection timed out. Check the Backend Realtime status and retry.',
      )
    } catch (error) {
      await realtimeAgent.abortCapture().catch(() => undefined)
      await resetStalledConnection(
        realtimeAgent as unknown as ConnectionGuardedRealtimeAgent,
      )
      throw error
    }
  }

  realtimeAgent.endCapture = async (): Promise<string> => {
    try {
      return await originalEndCapture()
    } catch (error) {
      await realtimeAgent.abortCapture().catch(() => undefined)
      throw error
    }
  }

  guardedAgent[PATCH_FLAG] = true
}

/**
 * The Realtime model is used here as a speech-understanding layer. The original
 * prompt declared one UI-selected language, which could make the model translate
 * English speech into Chinese when the selector or persistent session was still
 * Chinese. This patch makes the spoken audio authoritative and forbids translation.
 */
export function installVerbatimLanguageTranscription(): void {
  const patchableAgent = realtimeAgent as unknown as PatchableRealtimeAgent
  if (patchableAgent[LANGUAGE_PATCH_FLAG]) return

  patchableAgent.transcriptionInstructions = (): string => `
You are a multilingual speech transcription and correction layer, not a conversational assistant.
Return only the user's final intended utterance as normalized plain text. Do not answer the user.

LANGUAGE PRESERVATION IS MANDATORY:
- Detect the language from the spoken audio itself.
- Transcribe each segment in the same language in which it was spoken.
- Never translate English speech into Chinese.
- Never translate Chinese speech into English.
- If the user speaks entirely in English, the output must contain no Chinese translation.
- If the user speaks entirely in Chinese, output Chinese while preserving necessary English product names and technical terms.
- Preserve genuine code-switching instead of translating either language.
- Any UI language selection or earlier session language is only an acoustic hint. It must never force the transcript into that language.

Later explicit corrections override earlier uncertain words.
Chinese correction signals such as “不”, “不是”, “不对”, “我是说”, “应该是” and character explanations are authoritative.
English correction signals such as “no”, “not that”, “I mean”, “sorry”, “correction”, and “it should be” are authoritative.
Example Chinese correction: “打开钱会色的演示，不对，是浅灰色，深浅的浅，灰色的灰” becomes “打开浅灰色的演示”.
Example English correction: “Go to slide fourteen—sorry, I mean slide four” becomes “Go to slide four”.
Relevant terms include Microsoft Teams, PowerPoint, Word, Excel, Outlook, OneNote, meeting, presentation, mute, camera, next slide, previous slide, and screen sharing.
Never invent a request. If genuinely unintelligible, output exactly __UNCLEAR__.
Output only normalized plain text without labels, JSON, Markdown, quotation marks, explanations, or translations.
`.trim()

  patchableAgent[LANGUAGE_PATCH_FLAG] = true
}

installRealtimeConnectionGuard()
installRealtimeCaptureCleanup()
installVerbatimLanguageTranscription()
