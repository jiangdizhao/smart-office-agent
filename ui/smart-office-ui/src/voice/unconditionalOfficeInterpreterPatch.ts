import {
  realtimeOfficeInterpreter,
  type RealtimeOfficeDecision,
  type RealtimeOfficeToolCall,
} from './realtimeOfficeInterpreter'

declare global {
  interface Window {
    __SMART_OFFICE_UNCONDITIONAL_PPT_PATCH_INSTALLED__?: boolean
  }
}

function toolCall(steps: Array<Record<string, unknown>>): RealtimeOfficeToolCall {
  return {
    name: 'office_plan',
    arguments: { steps },
    call_id: null,
    source: 'gpt_realtime',
  }
}

function plan(steps: Array<Record<string, unknown>>): RealtimeOfficeDecision {
  console.info('[OfficePlan]', {
    source: 'unconditional_exhibition_ppt_patch',
    steps,
  })
  return { kind: 'tool_call', toolCall: toolCall(steps) }
}

function chineseNumber(token: string): number | null {
  if (/^\d+$/.test(token)) return Number(token)
  const digits: Record<string, number> = {
    零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4,
    五: 5, 六: 6, 七: 7, 八: 8, 九: 9,
  }
  if (!/^[零〇一二两三四五六七八九十百]+$/.test(token)) return null
  let total = 0
  let digit = 0
  for (const char of token) {
    if (char in digits) {
      digit = digits[char]
    } else if (char === '十') {
      total += (digit || 1) * 10
      digit = 0
    } else if (char === '百') {
      total += (digit || 1) * 100
      digit = 0
    }
  }
  return total + digit
}

function pptDecision(text: string): RealtimeOfficeDecision | null {
  const source = text.normalize('NFKC').toLocaleLowerCase()
    .replace(/\bp\s*[.\-_]?\s*p\s*[.\-_]?\s*t\b/gi, 'ppt')
    .replace(/\bpower\s+point\b/gi, 'powerpoint')
    .replace(/幻\s*灯\s*片/g, '幻灯片')
    .replace(/\s+/g, ' ')
    .trim()
  if (!source) return null
  if (/不要|别|无需|不用|请勿|do\s+not|don't|without/i.test(source)) return null

  const mentioned = /ppt|powerpoint|幻灯片|演示文稿|presentation|\bslides?\b|slideshow/i.test(source)
  const contextual = /下一页|下一张|后一页|后一张|往后|向后|继续|上一页|上一张|前一页|前一张|往前|向前|返回|翻回去|最后一页|末页|第\s*[一二两三四五六七八九十百\d]+\s*[页张]|next|previous|go\s+back|last\s+slide/i.test(source)
  if (!mentioned && !contextual) return null

  if (/下一页|下一张|后一页|后一张|往后|向后|继续|next/i.test(source)) {
    return plan([{ name: 'presentation_next_slide' }])
  }
  if (/上一页|上一张|前一页|前一张|往前|向前|返回|翻回去|previous|go\s+back/i.test(source)) {
    return plan([{ name: 'presentation_previous_slide' }])
  }
  if (/最后一页|末页|last\s+slide|final\s+slide/i.test(source)) {
    return plan([{ name: 'presentation_go_to_slide', slide_target: 'last' }])
  }
  const page = source.match(/(?:跳到|转到|翻到|前往)?\s*第?\s*([一二两三四五六七八九十百\d]+)\s*[页张]|(?:go|jump|move)\s+to\s+(?:slide\s+)?(\d+)/i)
  if (page) {
    const value = chineseNumber(page[1] ?? page[2])
    if (value !== null && value >= 1) {
      return plan([{ name: 'presentation_go_to_slide', slide_number: value }])
    }
  }
  if (/结束|停止|退出放映|end|stop|exit/i.test(source)) {
    return plan([{ name: 'presentation_end_slideshow' }])
  }
  if (/关闭|关掉|close|quit/i.test(source)) {
    return plan([{ name: 'presentation_close' }])
  }
  if (/状态|第几页|多少页|current\s+slide|status/i.test(source)) {
    return plan([{ name: 'presentation_get_status' }])
  }
  if (/开始|播放|演示|展示|放映|全屏|present|show|play|run|start/i.test(source)) {
    return plan([{ name: 'presentation_start_slideshow' }])
  }
  if (/打开|开启|启动|调出|弄出|open|launch/i.test(source)) {
    return plan([{ name: 'presentation_open_configured' }])
  }

  // The exhibition contract treats every affirmative PPT mention as an action.
  // A bare mention or a question therefore opens the configured deck and starts it
  // instead of reaching ordinary conversation.
  return plan([
    { name: 'presentation_open_configured' },
    { name: 'presentation_start_slideshow' },
  ])
}

export function installUnconditionalOfficeInterpreterPatch(): void {
  if (window.__SMART_OFFICE_UNCONDITIONAL_PPT_PATCH_INSTALLED__) return
  window.__SMART_OFFICE_UNCONDITIONAL_PPT_PATCH_INSTALLED__ = true
  const original = realtimeOfficeInterpreter.interpret.bind(realtimeOfficeInterpreter)
  realtimeOfficeInterpreter.interpret = async (text, language) => {
    const direct = pptDecision(text)
    if (direct) return direct
    return await original(text, language)
  }
}

installUnconditionalOfficeInterpreterPatch()
