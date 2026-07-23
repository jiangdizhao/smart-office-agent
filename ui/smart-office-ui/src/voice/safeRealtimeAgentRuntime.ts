import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'

const PATCH_FLAG = '__smartOfficeCaptureCleanupInstalled__'
const LANGUAGE_PATCH_FLAG = '__smartOfficeVerbatimLanguageTranscriptionInstalled__'

type GuardedRealtimeAgent = typeof realtimeAgent & {
  [PATCH_FLAG]?: boolean
}

type PatchableRealtimeAgent = {
  [LANGUAGE_PATCH_FLAG]?: boolean
  transcriptionInstructions: () => string
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
      await originalBeginCapture(language)
    } catch (error) {
      await realtimeAgent.abortCapture().catch(() => undefined)
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

installRealtimeCaptureCleanup()
installVerbatimLanguageTranscription()
