const PRESENTATION_TERM = /(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿)/i
const PRESENTATION_QUESTION = /(?:是什么|什么是|怎么|如何|为什么|介绍|功能|用途|能做什么|有什么用|what\s+is|what\s+does|why|how\s+do|how\s+does|tell\s+me|explain|describe)/i
const OPEN_PRESENTATION = /(?:打开|开启|启动|运行|\bopen\b|\blaunch\b).{0,10}(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿)|(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿).{0,10}(?:打开|开启|启动|运行|\bopen\b|\blaunch\b)/i
const CLOSE_PRESENTATION = /(?:关闭|关掉|退出|\bclose\b|\bquit\b|\bexit\b).{0,10}(?:power\s*point|powerpoint|\bppt\b|演示文稿)|(?:power\s*point|powerpoint|\bppt\b|演示文稿).{0,10}(?:关闭|关掉|退出|\bclose\b|\bquit\b|\bexit\b)/i
const NEGATED_CLOSE_PRESENTATION = /(?:不要|别|无需|不需要|do\s*not|don't|without).{0,8}(?:关闭|关掉|退出|close|quit|exit).{0,10}(?:power\s*point|powerpoint|\bppt\b|演示文稿)/i
const START_SLIDESHOW = /(?:开始|启动|进入|全屏|播放).{0,10}(?:演示|放映|幻灯片|演示文稿|power\s*point|powerpoint|\bppt\b|slide\s*show|slideshow|presentation)|(?:演示|放映|幻灯片|演示文稿|power\s*point|powerpoint|\bppt\b|slide\s*show|slideshow|presentation).{0,10}(?:开始|启动|进入|全屏|播放)|\b(?:start|begin|run|play)\b.{0,18}\b(?:slide\s*show|slideshow|presentation)\b/i
const DEMONSTRATE_PRESENTATION = /(?:演示|展示|播放).{0,10}(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿)|(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿).{0,10}(?:演示|展示|播放)|\b(?:demonstrate|show|present)\b.{0,18}\b(?:power\s*point|powerpoint|ppt|slides?|presentation)\b/i
const NEXT_SLIDE = /(?:下一页|下一张|后一页|后一张|往后翻|向后翻|继续往下)|\b(?:next\s+slide|advance|move\s+forward)\b/i
const PREVIOUS_SLIDE = /(?:上一页|上一张|前一页|前一张|往前翻|向前翻|翻回前面)|\b(?:previous\s+slide|go\s+back|move\s+back(?:ward)?)\b/i
const END_SLIDESHOW = /(?:结束|停止|退出|关闭).{0,10}(?:演示|放映|幻灯片放映)|(?:演示|放映|幻灯片放映).{0,10}(?:结束|停止|退出|关闭)|\b(?:end|stop|exit)\b.{0,18}\b(?:slide\s*show|slideshow|presentation)\b/i
const PRESENTATION_STATUS = /(?:第几页|哪一页|当前页|现在是第几页|演示状态|放映状态)|\b(?:what|which)\s+slide\b|\bpresentation\s+status\b/i
const GOTO_OR_MULTI_PAGE = /(?:跳到|转到|前往|最后一页|末页|第\s*[一二三四五六七八九十百\d]+\s*页|翻\s*[一二三四五六七八九十百\d]+\s*页)|\b(?:go\s+to|jump\s+to|last\s+slide|final\s+slide|\d+(?:st|nd|rd|th)?\s+slide|\d+\s+slides?)\b/i
const OTHER_DOMAIN = /(?:teams|one\s*note|onenote|outlook|邮箱|邮件|音乐|歌曲|音量|声音|亮度|预约|登记|联系人|会议总结|草稿|收件人|volume|brightness|music|booking|appointment|contact|email|draft)/i

export type PresentationPlanStep = Record<string, unknown>

export function deterministicPresentationSteps(
  text: string,
): PresentationPlanStep[] | null {
  const clean = text.normalize('NFKC').replace(/\s+/g, ' ').trim()
  if (!clean || PRESENTATION_QUESTION.test(clean)) return null
  if (GOTO_OR_MULTI_PAGE.test(clean) || OTHER_DOMAIN.test(clean)) return null

  const hasTerm = PRESENTATION_TERM.test(clean)
  const hasOpen = hasTerm && OPEN_PRESENTATION.test(clean)
  const hasClose = hasTerm
    && CLOSE_PRESENTATION.test(clean)
    && !NEGATED_CLOSE_PRESENTATION.test(clean)
  const hasDemonstrate = hasTerm && DEMONSTRATE_PRESENTATION.test(clean)
  const hasStart = START_SLIDESHOW.test(clean)
  const hasEnd = END_SLIDESHOW.test(clean)
  const hasNext = NEXT_SLIDE.test(clean)
  const hasPrevious = PREVIOUS_SLIDE.test(clean)
  const hasStatus = PRESENTATION_STATUS.test(clean)

  const controlCount = [hasClose, hasEnd, hasNext, hasPrevious, hasStatus]
    .filter(Boolean).length
  const startLike = hasDemonstrate || hasStart

  if (startLike && controlCount === 0) {
    if (hasDemonstrate || hasOpen) {
      return [
        { name: 'presentation_open_configured' },
        { name: 'presentation_start_slideshow' },
      ]
    }
    return [{ name: 'presentation_start_slideshow' }]
  }

  if (hasOpen && !startLike && controlCount > 0) return null
  if (controlCount !== 1 || startLike || hasOpen) return null

  if (hasClose) return [{ name: 'presentation_close' }]
  if (hasEnd) return [{ name: 'presentation_end_slideshow' }]
  if (hasNext) return [{ name: 'presentation_next_slide' }]
  if (hasPrevious) return [{ name: 'presentation_previous_slide' }]
  if (hasStatus) return [{ name: 'presentation_get_status' }]
  return null
}
