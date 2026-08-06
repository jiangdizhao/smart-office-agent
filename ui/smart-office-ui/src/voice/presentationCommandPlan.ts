const PRESENTATION_TERM = /(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿|\bslides?\b|slide\s*show|slideshow|presentation)/i
const OPEN_PRESENTATION = /(?:打开|开启|启动|运行|调出|弄出|\bopen\b|\blaunch\b).{0,14}(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿|presentation)|(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿|presentation).{0,14}(?:打开|开启|启动|运行|调出|弄出|\bopen\b|\blaunch\b)/i
const CLOSE_PRESENTATION = /(?:关闭|关掉|退出|\bclose\b|\bquit\b|\bexit\b).{0,14}(?:power\s*point|powerpoint|\bppt\b|演示文稿|presentation)|(?:power\s*point|powerpoint|\bppt\b|演示文稿|presentation).{0,14}(?:关闭|关掉|退出|\bclose\b|\bquit\b|\bexit\b)/i
const NEGATED_CLOSE_PRESENTATION = /(?:不要|别|无需|不需要|do\s*not|don't|without).{0,10}(?:关闭|关掉|退出|close|quit|exit)/i
const START_SLIDESHOW = /(?:开始|启动|进入|全屏|播放|演示|展示|放映).{0,14}(?:演示|放映|幻灯片|演示文稿|power\s*point|powerpoint|\bppt\b|slide\s*show|slideshow|presentation)|(?:演示|放映|幻灯片|演示文稿|power\s*point|powerpoint|\bppt\b|slide\s*show|slideshow|presentation).{0,14}(?:开始|启动|进入|全屏|播放|演示|展示|放映)|\b(?:start|begin|run|play|demonstrate|show|present)\b.{0,20}\b(?:power\s*point|powerpoint|ppt|slides?|slide\s*show|slideshow|presentation)\b/i
const NEXT_SLIDE = /(?:下一页|下一张|后一页|后一张|往后翻|向后翻|继续往下)|\b(?:next\s+slide|advance|move\s+forward)\b/i
const PREVIOUS_SLIDE = /(?:上一页|上一张|前一页|前一张|往前翻|向前翻|翻回前面)|\b(?:previous\s+slide|go\s+back|move\s+back(?:ward)?)\b/i
const END_SLIDESHOW = /(?:结束|停止|退出|关闭).{0,12}(?:演示|放映|幻灯片放映)|(?:演示|放映|幻灯片放映).{0,12}(?:结束|停止|退出|关闭)|\b(?:end|stop|exit)\b.{0,20}\b(?:slide\s*show|slideshow|presentation)\b/i
const PRESENTATION_STATUS = /(?:第几页|哪一页|当前页|现在是第几页|演示状态|放映状态)|\b(?:what|which)\s+slide\b|\bpresentation\s+status\b/i
const CURRENT_SLIDE_SUMMARY = /(?:总结|概括|归纳|提炼|摘要|summari[sz]e|recap).{0,14}(?:当前|这一|这张|本页|这一页|幻灯片|ppt|powerpoint|slide|presentation)|(?:当前|这一|这张|本页|这一页|幻灯片|ppt|powerpoint|slide|presentation).{0,14}(?:总结|概括|归纳|提炼|摘要|summari[sz]e|recap)/i
const CURRENT_SLIDE_EXPLANATION = /(?:理解|解释|分析|讲解|说明|看懂|什么意思|想表达什么|explain|interpret|analyse|analyze|understand).{0,14}(?:当前|这一|这张|本页|这一页|幻灯片|ppt|powerpoint|slide|presentation)|(?:当前|这一|这张|本页|这一页|幻灯片|ppt|powerpoint|slide|presentation).{0,14}(?:理解|解释|分析|讲解|说明|什么意思|想表达什么|explain|interpret|analyse|analyze|understand)/i
const LAST_SLIDE = /(?:最后一页|末页)|\b(?:last|final)\s+slide\b/i
const GOTO_SLIDE = /(?:跳到|转到|前往|翻到)\s*第?\s*([一二三四五六七八九十百\d]+)\s*(?:页|张)|(?:第\s*([一二三四五六七八九十百\d]+)\s*(?:页|张))|\b(?:go|jump|move)\s+to\s+(?:slide\s+)?(\d+)\b/i
const OTHER_DOMAIN = /(?:teams|one\s*note|onenote|outlook|邮箱|邮件|音乐|歌曲|音量|声音|亮度|预约|登记|联系人|会议总结|草稿|收件人|volume|brightness|music|booking|appointment|contact|email|draft)/i

const CHINESE_DIGITS: Record<string, number> = {
  零: 0,
  〇: 0,
  一: 1,
  二: 2,
  两: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
  七: 7,
  八: 8,
  九: 9,
}

export type PresentationPlanStep = Record<string, unknown>
export type CurrentSlideInsightMode = 'summary' | 'explanation'

export function mentionsPresentation(text: string): boolean {
  return PRESENTATION_TERM.test(text.normalize('NFKC'))
}

export function currentSlideInsightMode(text: string): CurrentSlideInsightMode | null {
  const clean = text.normalize('NFKC').replace(/\s+/g, ' ').trim()
  if (CURRENT_SLIDE_EXPLANATION.test(clean)) return 'explanation'
  if (CURRENT_SLIDE_SUMMARY.test(clean)) return 'summary'
  return null
}

export function presentationClarification(language: 'zh' | 'en'): string {
  return language === 'zh'
    ? '请告诉我您要打开、播放、翻页、跳页、总结当前页，还是解释当前页 PPT。'
    : 'Tell me whether to open, play, change slides, jump to a slide, summarize the current slide, or explain it.'
}

function parseChineseInteger(token: string): number | null {
  if (/^\d+$/.test(token)) return Number(token)
  if (!/^[零〇一二两三四五六七八九十百]+$/.test(token)) return null
  let total = 0
  let digit = 0
  for (const char of token) {
    if (char in CHINESE_DIGITS) {
      digit = CHINESE_DIGITS[char]
      continue
    }
    if (char === '十') {
      total += (digit || 1) * 10
      digit = 0
      continue
    }
    if (char === '百') {
      total += (digit || 1) * 100
      digit = 0
      continue
    }
    return null
  }
  return total + digit
}

function requestedSlideNumber(clean: string): number | null {
  const match = clean.match(GOTO_SLIDE)
  const token = match?.[1] ?? match?.[2] ?? match?.[3]
  if (!token) return null
  const value = parseChineseInteger(token)
  return value !== null && Number.isInteger(value) && value >= 1 ? value : null
}

export function deterministicPresentationSteps(
  text: string,
): PresentationPlanStep[] | null {
  const clean = text.normalize('NFKC').replace(/\s+/g, ' ').trim()
  if (!clean) return null

  const hasTerm = mentionsPresentation(clean)
  const contextualControl = NEXT_SLIDE.test(clean)
    || PREVIOUS_SLIDE.test(clean)
    || END_SLIDESHOW.test(clean)
    || PRESENTATION_STATUS.test(clean)
    || LAST_SLIDE.test(clean)
    || GOTO_SLIDE.test(clean)
    || currentSlideInsightMode(clean) !== null
  if (!hasTerm && !contextualControl) return null

  // Current-slide insight is handled by a dedicated fast API before the Office
  // interpreter. Returning null here prevents an unsupported plan action if that
  // direct path is unavailable; the caller will keep the request in PPT-specific
  // clarification instead of falling into general chat.
  if (currentSlideInsightMode(clean) !== null) return null

  if (LAST_SLIDE.test(clean)) {
    return [
      { name: 'presentation_start_slideshow' },
      { name: 'presentation_go_to_slide', slide_target: 'last' },
    ]
  }
  const slideNumber = requestedSlideNumber(clean)
  if (slideNumber !== null) {
    return [
      { name: 'presentation_start_slideshow' },
      { name: 'presentation_go_to_slide', slide_number: slideNumber },
    ]
  }
  if (NEXT_SLIDE.test(clean)) {
    return [
      { name: 'presentation_start_slideshow' },
      { name: 'presentation_next_slide' },
    ]
  }
  if (PREVIOUS_SLIDE.test(clean)) {
    return [
      { name: 'presentation_start_slideshow' },
      { name: 'presentation_previous_slide' },
    ]
  }
  if (PRESENTATION_STATUS.test(clean)) return [{ name: 'presentation_get_status' }]
  if (END_SLIDESHOW.test(clean)) return [{ name: 'presentation_end_slideshow' }]

  const hasClose = CLOSE_PRESENTATION.test(clean) && !NEGATED_CLOSE_PRESENTATION.test(clean)
  if (hasClose) return [{ name: 'presentation_close' }]

  const hasOpen = OPEN_PRESENTATION.test(clean)
  const hasStart = START_SLIDESHOW.test(clean)
  if (hasStart) {
    return hasOpen
      ? [
          { name: 'presentation_open_configured' },
          { name: 'presentation_start_slideshow' },
        ]
      : [{ name: 'presentation_start_slideshow' }]
  }
  if (hasOpen) return [{ name: 'presentation_open_configured' }]

  if (OTHER_DOMAIN.test(clean)) return null
  return null
}
