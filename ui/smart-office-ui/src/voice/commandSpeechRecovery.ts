import {
  realtimeAgent,
  type VoiceLanguage,
} from './realtimeAgentRuntime'

let installed = false
let activeLanguage: VoiceLanguage = 'zh'

const APP_PATTERNS = {
  teams: /(?:microsoft\s*)?teams|微软团队/i,
  onenote: /one\s*note|onenote|微软笔记/i,
  powerpoint: /power\s*point|powerpoint|ppt|幻灯片/i,
  outlook: /out\s*look|outlook|邮箱|邮件/i,
  music: /music|song|歌曲|音乐|放歌|听歌/i,
} as const

const APP_REMOVE_PATTERNS: Record<keyof typeof APP_PATTERNS, RegExp> = {
  teams: /(?:microsoft\s*)?teams|微软团队/gi,
  onenote: /one\s*note|onenote|微软笔记/gi,
  powerpoint: /power\s*point|powerpoint|ppt|幻灯片/gi,
  outlook: /out\s*look|outlook|邮箱|邮件/gi,
  music: /music|song|歌曲|音乐|放歌|听歌/gi,
}

export type CommandTarget = keyof typeof APP_PATTERNS
export type CommandAction = 'open' | 'close' | 'play' | 'stop' | null

const OPEN_PATTERN = /(?:打开|开启|启动|运行|open|launch|start|turn\s*on)/i
const CLOSE_PATTERN = /(?:关闭|关掉|退出|结束|close|quit|exit|turn\s*off)/i
const PLAY_PATTERN = /(?:播放|放一首|放点|来一首|play|start)/i
const STOP_PATTERN = /(?:停止|别放了|不要播放|stop|close|turn\s*off)/i

// Known phonetic failures observed on the exhibition microphone. These mappings
// are deliberately narrow: they only apply when a supported application name is
// also present, so ordinary English conversation is not rewritten.
const PHONETIC_CLOSE_PATTERN = /(?:\bone\s*b(?:ee)?\b|\b1\s*b\b|\bwan\s*bi\b|\bguan\s*bi\b|\bkwan\s*bee\b|\b关\s*闭\b)/i
const PHONETIC_OPEN_PATTERN = /(?:\bda\s*kai\b|\bdakai\b|\bta\s*kai\b|\bthe\s*guy\b|\b打\s*开\b)/i
const NON_COMMAND_CONTEXT = /(?:是什么|什么是|怎么|如何|为什么|介绍|功能|用途|能做什么|有什么用|what\s+is|what\s+does|why|how\s+do|how\s+does|tell\s+me|explain|describe)/i
const BOUNDED_FILLER = /(?:请|帮我|麻烦|给我|现在|一下|好吗|可以吗|能否|能不能|命令|操作|please|could\s+you|would\s+you|can\s+you|for\s+me|now|uh|um|erm|hmm|xx+|x|嗯|呃|啊|那个)/gi

function cleanTranscript(value: string): string {
  return value
    .normalize('NFKC')
    .replace(/[，。！？、;；:：]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function targetFromTranscript(text: string): CommandTarget | null {
  for (const [target, pattern] of Object.entries(APP_PATTERNS) as Array<
    [CommandTarget, RegExp]
  >) {
    if (pattern.test(text)) return target
  }
  return null
}

function actionFromTranscript(text: string, target: CommandTarget): CommandAction {
  if (target === 'music') {
    if (STOP_PATTERN.test(text) || PHONETIC_CLOSE_PATTERN.test(text)) return 'stop'
    if (PLAY_PATTERN.test(text) || PHONETIC_OPEN_PATTERN.test(text)) return 'play'
    return null
  }
  if (CLOSE_PATTERN.test(text) || PHONETIC_CLOSE_PATTERN.test(text)) return 'close'
  if (OPEN_PATTERN.test(text) || PHONETIC_OPEN_PATTERN.test(text)) return 'open'
  return null
}

function isBoundedCommandFragment(text: string, target: CommandTarget): boolean {
  if (NON_COMMAND_CONTEXT.test(text)) return false
  const remainder = text
    .replace(APP_REMOVE_PATTERNS[target], ' ')
    .replace(BOUNDED_FILLER, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  // A bare product name or a product name surrounded only by short filler/ASR
  // debris is a bounded command fragment. Longer semantic content remains normal
  // conversation and must not be rewritten into an open/close prompt.
  return remainder.length === 0 || remainder.replace(/\s+/g, '').length <= 3
}

function canonicalTarget(target: CommandTarget): string {
  if (target === 'teams') return 'Teams'
  if (target === 'onenote') return 'OneNote'
  if (target === 'powerpoint') return 'PowerPoint'
  if (target === 'outlook') return 'Outlook'
  return 'music'
}

function canonicalCommand(
  target: CommandTarget,
  action: CommandAction,
  language: VoiceLanguage,
): string {
  const app = canonicalTarget(target)
  if (language === 'en') {
    if (target === 'music' && action === 'play') return 'play music'
    if (target === 'music' && action === 'stop') return 'stop music'
    if (action === 'open') return `open ${app}`
    if (action === 'close') return `close ${app}`
    return app
  }
  if (target === 'music' && action === 'play') return '播放音乐'
  if (target === 'music' && action === 'stop') return '关闭音乐'
  if (action === 'open') return `打开 ${app}`
  if (action === 'close') return `关闭 ${app}`
  // Keep a Chinese marker so a Chinese Visit cannot be switched to English merely
  // because the ASR returned only an English product name.
  return target === 'music' ? '音乐命令' : `${app} 命令`
}

export type RecoveredCommandTranscript = {
  raw: string
  normalized: string
  target: CommandTarget | null
  action: CommandAction
  ambiguous: boolean
  language: VoiceLanguage
  recovered: boolean
}

export function recoverCommandTranscript(
  value: string,
  language: VoiceLanguage = activeLanguage,
): RecoveredCommandTranscript {
  const raw = value.trim()
  const clean = cleanTranscript(raw)
  const target = targetFromTranscript(clean)
  if (!target) {
    return {
      raw,
      normalized: raw,
      target: null,
      action: null,
      ambiguous: false,
      language,
      recovered: false,
    }
  }

  const action = actionFromTranscript(clean, target)
  if (action === null && !isBoundedCommandFragment(clean, target)) {
    return {
      raw,
      normalized: raw,
      target: null,
      action: null,
      ambiguous: false,
      language,
      recovered: false,
    }
  }

  const normalized = canonicalCommand(target, action, language)
  const recovered = normalized.toLocaleLowerCase() !== raw.toLocaleLowerCase()
  if (recovered) {
    console.info('[RealtimeDiagnostics] command-transcript-recovered', {
      raw,
      normalized,
      target,
      action,
      visitLanguage: language,
    })
  }
  return {
    raw,
    normalized,
    target,
    action,
    ambiguous: action === null,
    language,
    recovered,
  }
}

export function commandClarification(
  recovered: RecoveredCommandTranscript,
): string | null {
  if (!recovered.target || !recovered.ambiguous) return null
  const app = canonicalTarget(recovered.target)
  if (recovered.language === 'en') {
    if (recovered.target === 'music') return 'Would you like me to play music or stop the music?'
    return `Would you like me to open or close ${app}?`
  }
  if (recovered.target === 'music') return '您是要播放音乐，还是关闭音乐？'
  return `您是要打开还是关闭 ${app}？`
}

function transcriptionPrompt(language: VoiceLanguage): string {
  const languageRule = language === 'zh'
    ? `The active visitor language is Chinese. Treat Chinese as a strong prior. Application names such as Teams, OneNote, PowerPoint, Outlook and PPT may be spoken in English, but nearby action words are normally Chinese. Do not switch the whole utterance to English merely because an application name is English. In particular, Chinese “关闭” (guan bi) must not be rendered as “one B”, “one bee”, “1B”, “wan bi” or similar English-looking text; normalize it to 关闭. Chinese “打开” (da kai) must not be rendered as unrelated English words; normalize it to 打开.`
    : `The active visitor language is English. Preserve genuine Chinese words only when they are clearly spoken; otherwise transcribe the command in English.`
  return `
You are a multilingual speech transcription and command-correction layer, not a conversational assistant.
Return only the user's final intended utterance as normalized plain text. Do not answer the user.
${languageRule}
Preserve genuine Chinese-English code-switching and keep Microsoft product names in their conventional form.
Use the active visitor language as the response-language prior; do not infer a language switch from a product name alone.
Later explicit corrections override earlier uncertain words.
Relevant commands include:
打开 Teams / 关闭 Teams / open Teams / close Teams
打开 OneNote / 关闭 OneNote / open OneNote / close OneNote
打开 PowerPoint / 关闭 PowerPoint / open PowerPoint / close PowerPoint
打开 Outlook / 关闭 Outlook / open Outlook / close Outlook
播放音乐 / 关闭音乐 / play music / stop music
Relevant terms also include meeting, presentation, mute, camera, next slide, previous slide, and screen sharing.
Never invent a request. When the application name is clear but the action is genuinely unclear, return only the application name so the application can ask a bounded clarification.
If the complete utterance is genuinely unintelligible, output exactly __UNCLEAR__.
Output only normalized plain text without labels, JSON, Markdown, quotation marks, explanations, or translations.
`.trim()
}

export function installCommandSpeechRecovery(): void {
  if (installed) return
  installed = true

  const originalPrewarm = realtimeAgent.prewarm.bind(realtimeAgent)
  realtimeAgent.prewarm = async (language, signal) => {
    activeLanguage = language
    return await originalPrewarm(language, signal)
  }

  const originalBeginCapture = realtimeAgent.beginCapture.bind(realtimeAgent)
  realtimeAgent.beginCapture = async (language, signal) => {
    activeLanguage = language
    return await originalBeginCapture(language, signal)
  }

  const originalStartContinuousCapture = realtimeAgent.startContinuousCapture.bind(realtimeAgent)
  realtimeAgent.startContinuousCapture = async (language, signal) => {
    activeLanguage = language
    return await originalStartContinuousCapture(language, signal)
  }

  realtimeAgent.transcriptionInstructions = () => transcriptionPrompt(activeLanguage)

  const originalNextContinuousUtterance = realtimeAgent.nextContinuousUtterance.bind(realtimeAgent)
  realtimeAgent.nextContinuousUtterance = async (signal) => {
    const transcript = await originalNextContinuousUtterance(signal)
    return recoverCommandTranscript(transcript, activeLanguage).normalized
  }

  const originalEndCapture = realtimeAgent.endCapture.bind(realtimeAgent)
  realtimeAgent.endCapture = async (signal) => {
    const transcript = await originalEndCapture(signal)
    return recoverCommandTranscript(transcript, activeLanguage).normalized
  }
}

installCommandSpeechRecovery()
