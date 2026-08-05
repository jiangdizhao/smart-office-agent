import type { VoiceLanguage } from './realtimeAgentRuntime'

export const VISIT_LANGUAGE_CHANGED_EVENT = 'smartoffice:visit-language-changed'

export type VisitLanguageChangeDetail = {
  language: VoiceLanguage
  reason: string
  explicit: boolean
}

const CHINESE_CHARACTER = /[\u3400-\u9fff]/g
const LATIN_WORD = /[A-Za-z]+(?:['’-][A-Za-z]+)*/g
const EXPLICIT_CHINESE = /(?:请|麻烦|可以|能不能)?(?:用|说|讲|改成|切换到)?(?:中文|汉语|普通话)(?:回答|交流|对话|说话)?|(?:please\s+)?speak\s+chinese|answer\s+in\s+chinese/i
const EXPLICIT_ENGLISH = /(?:请|麻烦|可以|能不能)?(?:用|说|讲|改成|切换到)?(?:英文|英语)(?:回答|交流|对话|说话)?|(?:please\s+)?speak\s+english|answer\s+in\s+english/i
const SHORT_CHINESE_GREETING = /^(?:你好|您好|早上好|下午好|晚上好|嗨|哈喽)[！!。.]?$/

let installed = false
let preferredLanguage: VoiceLanguage = 'en'

function publish(language: VoiceLanguage, reason: string, explicit: boolean): void {
  preferredLanguage = language
  const detail: VisitLanguageChangeDetail = { language, reason, explicit }
  window.dispatchEvent(new CustomEvent<VisitLanguageChangeDetail>(
    VISIT_LANGUAGE_CHANGED_EVENT,
    { detail },
  ))
  console.info('[VisitLanguage] changed', detail)
}

function predominantlyChinese(text: string): boolean {
  const clean = text.trim()
  if (!clean) return false
  if (SHORT_CHINESE_GREETING.test(clean)) return true

  const chineseCount = (clean.match(CHINESE_CHARACTER) ?? []).length
  if (chineseCount < 3) return false

  const latinWords = (clean.match(LATIN_WORD) ?? [])
    .filter((word) => !/^(?:teams|onenote|outlook|powerpoint|ppt|word|excel|smart|office|sara)$/i.test(word))
    .length
  return chineseCount >= 5 || chineseCount >= Math.max(3, latinWords * 2)
}

class VisitLanguagePreference {
  install(): void {
    if (installed) return
    installed = true

    const reset = () => this.reset('visit_boundary')
    const observe = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null
      const text = String(detail?.transcript ?? detail?.text ?? '').trim()
      if (text) this.resolve(text)
    }

    window.addEventListener('smartoffice:visit-activated', reset)
    window.addEventListener('smartoffice:visit-revoked', reset)
    window.addEventListener('smartoffice:continuous-user-transcript', observe)
    window.addEventListener('smartoffice:realtime-continuous-utterance', observe)
  }

  current(): VoiceLanguage {
    return preferredLanguage
  }

  reset(reason = 'manual_reset'): VoiceLanguage {
    if (preferredLanguage !== 'en') publish('en', reason, false)
    else preferredLanguage = 'en'
    return preferredLanguage
  }

  force(language: VoiceLanguage, reason = 'explicit_language_selection'): VoiceLanguage {
    if (preferredLanguage !== language) publish(language, reason, true)
    return preferredLanguage
  }

  resolve(text: string, current: VoiceLanguage = preferredLanguage): VoiceLanguage {
    const clean = text.trim()
    if (!clean) return current

    if (EXPLICIT_CHINESE.test(clean)) return this.force('zh', 'explicit_chinese_request')
    if (EXPLICIT_ENGLISH.test(clean)) return this.force('en', 'explicit_english_request')

    // Automatic detection is intentionally one-way within a Visit. English is the
    // exhibition default; a substantial Chinese utterance switches the Visit to
    // Chinese. It stays there until the visitor explicitly asks for English.
    if (preferredLanguage === 'en' && predominantlyChinese(clean)) {
      publish('zh', 'predominantly_chinese_utterance', false)
    }
    return preferredLanguage
  }
}

export const visitLanguagePreference = new VisitLanguagePreference()

export function installVisitLanguagePreference(): void {
  visitLanguagePreference.install()
}
