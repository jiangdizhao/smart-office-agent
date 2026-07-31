import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'
import { voiceOutputManager } from './voiceOutputManager'

const VISIT_FAREWELLS = new Set([
  '感谢您的来访，欢迎下次再来。',
  'Thank you for visiting. We hope to see you again soon.',
])

const originalSpeak = voiceOutputManager.speak.bind(voiceOutputManager)

voiceOutputManager.speak = async (text: string, language: VoiceLanguage): Promise<void> => {
  const closesVisit = VISIT_FAREWELLS.has(text.trim())
  try {
    await originalSpeak(text, language)
  } finally {
    if (closesVisit) {
      await realtimeAgent.shutdown().catch(() => undefined)
      window.dispatchEvent(new CustomEvent('smartoffice:realtime-visit-session-closed'))
    }
  }
}
