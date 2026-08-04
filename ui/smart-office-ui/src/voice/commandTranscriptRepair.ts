import type { VoiceLanguage } from './realtimeAgentRuntime'

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

const OPEN_PATTERN = /(?:打开|开启|启动|运行|\bopen\b|\blaunch\b|\bstart\b|turn\s*on)/i
const CLOSE_PATTERN = /(?:关闭|关掉|退出|结束|\bclose\b|\bquit\b|\bexit\b|turn\s*off)/i
const PLAY_PATTERN = /(?:播放|放一首|放点|来一首|\bplay\b|\bstart\b)/i
const STOP_PATTERN = /(?:停止|别放了|不要播放|\bstop\b|\bclose\b|turn\s*off)/i
const OPEN_REMOVE_PATTERN = /(?:打开|开启|启动|运行|\bopen\b|\blaunch\b|\bstart\b|turn\s*on)/gi
const CLOSE_REMOVE_PATTERN = /(?:关闭|关掉|退出|结束|\bclose\b|\bquit\b|\bexit\b|turn\s*off)/gi
const PLAY_REMOVE_PATTERN = /(?:播放|放一首|放点|来一首|\bplay\b|\bstart\b)/gi
const STOP_REMOVE_PATTERN = /(?:停止|别放了|不要播放|\bstop\b|\bclose\b|turn\s*off)/gi
const EXPLICIT_ZH_ACTION = /(?:打开|开启|启动|运行|关闭|关掉|退出|结束|播放|放一首|放点|来一首|停止|别放了|不要播放)/i
const EXPLICIT_EN_ACTION = /(?:\bopen\b|\blaunch\b|\bstart\b|\bclose\b|\bquit\b|\bexit\b|\bplay\b|\bstop\b|turn\s+on|turn\s+off)/i
const PHONETIC_CLOSE_PATTERN = /(?:\bone\s*b(?:ee)?\b|\b1\s*b\b|\bwan\s*bi\b|\bguan\s*bi\b|\bkwan\s*bee\b|\b关\s*闭\b)/i
const PHONETIC_OPEN_PATTERN = /(?:\bda\s*kai\b|\bdakai\b|\bta\s*kai\b|\bthe\s*guy\b|\b打\s*开\b)/i
const PHONETIC_CLOSE_REMOVE_PATTERN = /(?:\bone\s*b(?:ee)?\b|\b1\s*b\b|\bwan\s*bi\b|\bguan\s*bi\b|\bkwan\s*bee\b|\b关\s*闭\b)/gi
const PHONETIC_OPEN_REMOVE_PATTERN = /(?:\bda\s*kai\b|\bdakai\b|\bta\s*kai\b|\bthe\s*guy\b|\b打\s*开\b)/gi
const NON_COMMAND_CONTEXT = /(?:是什么|什么是|怎么|如何|为什么|介绍|功能|用途|能做什么|有什么用|what\s+is|what\s+does|why|how\s+do|how\s+does|tell\s+me|explain|describe)/i
const BOUNDED_FILLER = /(?:请|帮我|麻烦|给我|现在|一下|好吗|可以吗|能否|能不能|命令|操作|please|could\s+you|would\s+you|can\s+you|for\s+me|now|uh|um|erm|hmm|xx+|x|嗯|呃|啊|那个)/gi

function cleanTranscript(value: string): string {
  return value
    .normalize('NFKC')
    .replace(/[，。！？、;；:：]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function targetsFromTranscript(text: string): CommandTarget[] {
  return (Object.entries(APP_PATTERNS) as Array<[CommandTarget, RegExp]>)
    .filter(([, pattern]) => pattern.test(text))
    .map(([target]) => target)
}

function actionKindsFromTranscript(text: string, target: CommandTarget): CommandAction[] {
  const actions: CommandAction[] = []
  if (target === 'music') {
    if (PLAY_PATTERN.test(text) || PHONETIC_OPEN_PATTERN.test(text)) actions.push('play')
    if (STOP_PATTERN.test(text) || PHONETIC_CLOSE_PATTERN.test(text)) actions.push('stop')
  } else {
    if (OPEN_PATTERN.test(text) || PHONETIC_OPEN_PATTERN.test(text)) actions.push('open')
    if (CLOSE_PATTERN.test(text) || PHONETIC_CLOSE_PATTERN.test(text)) actions.push('close')
  }
  return [...new Set(actions)]
}

function actionFromTranscript(text: string, target: CommandTarget): CommandAction {
  const actions = actionKindsFromTranscript(text, target)
  return actions.length === 1 ? actions[0] : null
}

function commandLanguage(text: string, fallback: VoiceLanguage): VoiceLanguage {
  const hasChineseAction = EXPLICIT_ZH_ACTION.test(text)
  const hasEnglishAction = EXPLICIT_EN_ACTION.test(text)
  if (hasChineseAction && !hasEnglishAction) return 'zh'
  if (hasEnglishAction && !hasChineseAction) return 'en'
  return fallback
}

function removeSelectedAction(text: string, target: CommandTarget, action: CommandAction): string {
  let result = text
  if (target === 'music' && action === 'play') {
    result = result.replace(PLAY_REMOVE_PATTERN, ' ').replace(PHONETIC_OPEN_REMOVE_PATTERN, ' ')
  } else if (target === 'music' && action === 'stop') {
    result = result.replace(STOP_REMOVE_PATTERN, ' ').replace(PHONETIC_CLOSE_REMOVE_PATTERN, ' ')
  } else if (action === 'open') {
    result = result.replace(OPEN_REMOVE_PATTERN, ' ').replace(PHONETIC_OPEN_REMOVE_PATTERN, ' ')
  } else if (action === 'close') {
    result = result.replace(CLOSE_REMOVE_PATTERN, ' ').replace(PHONETIC_CLOSE_REMOVE_PATTERN, ' ')
  }
  return result
}

function semanticRemainder(
  text: string,
  target: CommandTarget,
  action: CommandAction,
): string {
  return removeSelectedAction(text.replace(APP_REMOVE_PATTERNS[target], ' '), target, action)
    .replace(BOUNDED_FILLER, ' ')
    .replace(/[^\p{L}\p{N}@%]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function isSafeSingleActionCommand(
  text: string,
  targets: CommandTarget[],
  target: CommandTarget,
  action: CommandAction,
): boolean {
  if (!action || targets.length !== 1 || NON_COMMAND_CONTEXT.test(text)) return false
  if (actionKindsFromTranscript(text, target).length !== 1) return false
  return semanticRemainder(text, target, action).length === 0
}

function isBareTargetFragment(text: string, targets: CommandTarget[], target: CommandTarget): boolean {
  if (targets.length !== 1 || NON_COMMAND_CONTEXT.test(text)) return false
  return text
    .replace(APP_REMOVE_PATTERNS[target], ' ')
    .replace(BOUNDED_FILLER, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim().length === 0
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
  language: VoiceLanguage = 'zh',
): RecoveredCommandTranscript {
  const raw = value.trim()
  const clean = cleanTranscript(raw)
  const targets = targetsFromTranscript(clean)
  const target = targets.length === 1 ? targets[0] : null

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

  const resolvedLanguage = commandLanguage(clean, language)
  const action = actionFromTranscript(clean, target)
  if (isSafeSingleActionCommand(clean, targets, target, action)) {
    const normalized = canonicalCommand(target, action, resolvedLanguage)
    return {
      raw,
      normalized,
      target,
      action,
      ambiguous: false,
      language: resolvedLanguage,
      recovered: normalized.toLocaleLowerCase() !== raw.toLocaleLowerCase(),
    }
  }

  if (action === null && isBareTargetFragment(clean, targets, target)) {
    return {
      raw,
      normalized: canonicalCommand(target, null, resolvedLanguage),
      target,
      action: null,
      ambiguous: true,
      language: resolvedLanguage,
      recovered: false,
    }
  }

  return {
    raw,
    normalized: raw,
    target: null,
    action: null,
    ambiguous: false,
    language: resolvedLanguage,
    recovered: false,
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
