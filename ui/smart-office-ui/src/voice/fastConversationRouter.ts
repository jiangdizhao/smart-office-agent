import {
  generateSimpleRealtimeAnswer as generateSalesAwareAnswer,
  previewConversationRoute as previewSalesAwareRoute,
  type SalesAwareConversationRoute,
} from '../sales/salesConversationRouter'
import type { VoiceLanguage } from './realtimeAgentRuntime'
import type { VisitLease } from '../vision/visitLeaseRegistry'
import {
  startManagedBackgroundAction,
  type ManagedBackgroundAction,
} from './backgroundTaskRuntime'
import { installSemanticOfficeInterpreterBridge } from './semanticOfficeInterpreterBridge'

export type { FastConversationAnswerEngine } from './fastConversationRouterCore'
export type FastConversationRoute = SalesAwareConversationRoute

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://127.0.0.1:8000'
const OPTIMIZED_CONTEXT_PREFIX = '__SMART_OFFICE_OPTIMIZED_CONTEXT__:'
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

installSemanticOfficeInterpreterBridge()

type RouteRequest = {
  conversationId: string
  visitId: string | null
  text: string
  language: VoiceLanguage
  actor: 'visitor' | 'employee' | 'operator'
  lease: VisitLease | null
}

type OptimizedContext = {
  text: string
  purpose: 'background_action_accepted' | 'direct_volume_result'
}

type PriorityOfficeKind = 'presentation' | 'volume'

function optimizedContext(context: OptimizedContext): string {
  return `${OPTIMIZED_CONTEXT_PREFIX}${JSON.stringify(context)}`
}

function parseOptimizedContext(value: string): OptimizedContext | null {
  if (!value.startsWith(OPTIMIZED_CONTEXT_PREFIX)) return null
  try {
    const parsed = JSON.parse(value.slice(OPTIMIZED_CONTEXT_PREFIX.length)) as OptimizedContext
    return parsed?.text?.trim() ? parsed : null
  } catch {
    return null
  }
}

function normalize(text: string): string {
  return text
    .normalize('NFKC')
    .toLocaleLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
}

function stripPoliteness(text: string): string {
  return normalize(text)
    .replace(
      /^(?:可以麻烦你|可以麻烦您|可以帮我|麻烦你|麻烦您|请你|请您|帮我|帮忙|你能|您能|麻烦|请)\s*/u,
      '',
    )
    .replace(/(?:帮我|帮忙)?(?:一下|吧|谢谢|thank you|please)[。.!！]?$/iu, '')
    .trim()
}

function managedAction(text: string): ManagedBackgroundAction | null {
  const clean = stripPoliteness(text).replace(/[。.!！]/g, '').trim()
  if (/不要|别|无需|不用|是否|能否|能不能|为什么|怎么|如何|比较|对比|if\b|do not|don't|without/i.test(clean)) {
    return null
  }
  const exact: Record<string, ManagedBackgroundAction> = {
    '打开 teams': 'teams_open',
    '打开teams': 'teams_open',
    '启动 teams': 'teams_open',
    '启动teams': 'teams_open',
    'open teams': 'teams_open',
    'launch teams': 'teams_open',
    '关闭 teams': 'teams_close',
    '关闭teams': 'teams_close',
    '退出 teams': 'teams_close',
    '退出teams': 'teams_close',
    'close teams': 'teams_close',
    '打开 onenote': 'onenote_open',
    '打开onenote': 'onenote_open',
    '启动 onenote': 'onenote_open',
    '启动onenote': 'onenote_open',
    'open onenote': 'onenote_open',
    'launch onenote': 'onenote_open',
    '关闭 onenote': 'onenote_close',
    '关闭onenote': 'onenote_close',
    '退出 onenote': 'onenote_close',
    '退出onenote': 'onenote_close',
    'close onenote': 'onenote_close',
    '播放音乐': 'music_play_random',
    '随机播放一首音乐': 'music_play_random',
    'play music': 'music_play_random',
    '停止音乐': 'music_stop',
    '关闭音乐': 'music_stop',
    'stop music': 'music_stop',
  }
  return exact[clean] ?? null
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

function clearVolumePercent(text: string): number | null {
  const clean = stripPoliteness(text)
  if (/不要|别|无需|不用|是否|能否|能不能|为什么|怎么|如何|多少|几|if\b|do not|don't/i.test(clean)) {
    return null
  }
  const match = clean.match(
    /^(?:把|将)?(?:系统|电脑|扬声器)?(?:音量|声音)(?:调到|调至|调成|调整到|调整为|设置为|设为|改为|变成)\s*(?:百分之\s*)?([零〇一二两三四五六七八九十百\d]{1,6})\s*(?:%|％|percent)?$/iu,
  ) ?? clean.match(/^set\s+(?:the\s+)?volume\s+to\s+(\d{1,3})\s*(?:%|percent)?$/i)
  if (!match) return null
  const value = parseChineseInteger(match[1])
  return value !== null && Number.isInteger(value) && value >= 0 && value <= 100 ? value : null
}

function explicitPriorityOfficeAction(text: string): PriorityOfficeKind | null {
  const clean = stripPoliteness(text).replace(/[。.!！]/g, '').trim()
  if (!clean) return null

  // Discussion, hypothetical and negated wording must never execute. These guards
  // intentionally run before the positive action patterns. Polite requests such as
  // “能不能打开 PPT” and “Can you open PowerPoint?” remain executable requests.
  if (
    /不要|别|无需|不用|仅介绍|只介绍|解释|比较|对比|假设|如果|为什么|怎么实现|如何实现|是什么|有什么|do not|don't|without|explain|compare|imagine|hypothetical|what is|how does/i.test(clean)
  ) return null

  const volumeMentioned = /音量|系统声音|电脑声音|扬声器声音|\bvolume\b|\baudio volume\b/i.test(clean)
  const volumeAction = /调|设|改|提高|降低|增大|减小|升高|静音|取消静音|多少|当前|set|adjust|change|increase|decrease|raise|lower|turn up|turn down|mute|unmute|current/i.test(clean)
  if (volumeMentioned && volumeAction) return 'volume'

  const presentationMentioned = /ppt|power\s*point|powerpoint|幻灯片|演示文稿|presentation|slide\s*show|slideshow/i.test(clean)
  const presentationAction = /打开|开启|启动|执行|演示|放映|播放|开始|继续|结束|停止|关闭|退出|跳到|翻到|下一页|上一页|下一张|上一张|open|launch|start|begin|run|present|show|play|continue|end|stop|close|exit|go to|next slide|previous slide/i.test(clean)
  if (presentationMentioned && presentationAction) return 'presentation'

  if (/^(?:下一页|上一页|下一张|上一张|后一页|前一页|最后一页|结束放映|开始放映|next slide|previous slide|last slide|start the show|end the show)$/i.test(clean)) {
    return 'presentation'
  }
  return null
}

async function setVolumeDirect(percent: number, request: RouteRequest): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/office/system/volume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ percent }),
    signal: request.lease?.signal,
  })
  if (!response.ok) {
    throw new Error(`Direct volume action failed: ${response.status} ${await response.text()}`)
  }
  const payload = await response.json() as {
    ok?: boolean
    message?: string
    office_status?: { data?: { volume_percent?: unknown } }
  }
  const observed = payload.office_status?.data?.volume_percent
  if (payload.ok) {
    const actual = typeof observed === 'number' ? Math.round(observed) : percent
    return request.language === 'zh'
      ? `系统音量已调整并验证为 ${actual}%。`
      : `System volume was adjusted and verified at ${actual} percent.`
  }
  const detail = String(payload.message ?? '').trim()
  return request.language === 'zh'
    ? `音量操作没有完成。${detail || '请检查 Windows 音频设备。'}`
    : `The volume action did not complete. ${detail || 'Please check the Windows audio device.'}`
}

function localGeneralFastPath(request: RouteRequest): SalesAwareConversationRoute | null {
  const clean = normalize(request.text)
  if (!clean || clean.length > 180) return null

  // Pending sales answers, references and all business/Office/privacy language must
  // still pass through the unified semantic and sales routers.
  if (/^(可以|好|好的|行|愿意|不用|不用了|不了|不需要|yes|no|ok|okay|sure|not now)$/i.test(clean)) return null
  if (/这个|那个|刚才|上一个|下一个|第二个|它|this|that|it|previous|second/i.test(clean)) return null
  if (
    /smart\s*office|公司|产品|方案|解决方案|演示|体验|报价|价格|费用|成本|预约|会议|联系方式|登记|隐私|人脸|行业|职位|痛点|需求|销售|powerpoint|ppt|teams|onenote|outlook|音量|亮度|音乐|录音|结果中心|company|product|solution|demo|price|cost|booking|contact|privacy|industry/i.test(clean)
  ) return null
  if (/打开|关闭|启动|停止|创建|发送|调整|设置|播放|执行|open|close|launch|start|stop|create|send|set|adjust|play/i.test(clean)) return null

  const greetingOrThanks = /^(你好|您好|早上好|下午好|晚上好|谢谢|多谢|hello|hi|hey|good morning|good afternoon|good evening|thanks|thank you)[。.!！]?$/i.test(clean)
  const standaloneQuestion = /[？?]$/.test(clean)
    || /^(什么是|为什么|怎么会|如何理解|谁是|哪里|哪一个|多少|请解释|请介绍|告诉我)\S+/u.test(clean)
    || /^(what|why|how|who|where|when|which|could you explain|tell me about)\b/i.test(clean)
  if (!greetingOrThanks && !standaloneQuestion) return null

  return {
    route: 'realtime_direct',
    scene: 'reception',
    route_reason: 'local_conservative_non_action_fast_path',
    conversation_complexity: 'simple',
    answer_engine: 'realtime',
    recent_context: '',
    visit_id: request.visitId,
    semantic_decision: null,
    office_delegate_text: null,
  }
}

function immediateRoute(
  request: RouteRequest,
  text: string,
  reason: string,
  purpose: OptimizedContext['purpose'],
): SalesAwareConversationRoute {
  return {
    route: 'realtime_direct',
    scene: 'office',
    route_reason: reason,
    conversation_complexity: 'simple',
    answer_engine: 'realtime',
    recent_context: optimizedContext({ text, purpose }),
    visit_id: request.visitId,
    semantic_decision: null,
    office_delegate_text: null,
  }
}

function priorityOfficeRoute(
  request: RouteRequest,
  kind: PriorityOfficeKind,
): SalesAwareConversationRoute {
  return {
    route: 'office_direct',
    scene: 'office',
    route_reason: `deterministic_priority_office_action:${kind}`,
    conversation_complexity: 'not_applicable',
    answer_engine: 'office_interpreter',
    recent_context: '',
    visit_id: request.visitId,
    semantic_decision: null,
    office_delegate_text: null,
  }
}

export async function previewConversationRoute(
  request: RouteRequest,
): Promise<SalesAwareConversationRoute> {
  const startedAt = performance.now()
  const volume = clearVolumePercent(request.text)
  if (volume !== null) {
    const text = await setVolumeDirect(volume, request)
    console.info('[ConversationLatency] direct-volume-route-complete', {
      elapsedMs: Math.round(performance.now() - startedAt),
      percent: volume,
      visitId: request.visitId,
    })
    return immediateRoute(request, text, 'direct_scoped_volume_action', 'direct_volume_result')
  }

  const action = managedAction(request.text)
  if (action) {
    const acceptance = await startManagedBackgroundAction(action, request)
    console.info('[ConversationLatency] managed-background-action-accepted', {
      elapsedMs: Math.round(performance.now() - startedAt),
      action,
      taskId: acceptance.taskId,
      visitId: request.visitId,
    })
    return immediateRoute(
      request,
      acceptance.acceptedText,
      `background_managed_action:${action}`,
      'background_action_accepted',
    )
  }

  const priorityOffice = explicitPriorityOfficeAction(request.text)
  if (priorityOffice) {
    console.info('[ConversationLatency] deterministic-office-priority-route', {
      elapsedMs: Math.round(performance.now() - startedAt),
      kind: priorityOffice,
      visitId: request.visitId,
    })
    return priorityOfficeRoute(request, priorityOffice)
  }

  const local = localGeneralFastPath(request)
  if (local) {
    console.info('[ConversationLatency] local-general-fast-path', {
      elapsedMs: Math.round(performance.now() - startedAt),
      visitId: request.visitId,
    })
    return local
  }
  return await previewSalesAwareRoute(request)
}

export async function generateSimpleRealtimeAnswer(
  text: string,
  language: VoiceLanguage,
  recentContext: string,
  lease: VisitLease | null,
): Promise<string> {
  const optimized = parseOptimizedContext(recentContext)
  if (optimized) {
    return ensureOptimizedHumanLikeText(optimized.text.trim(), language, optimized.purpose)
  }
  return await generateSalesAwareAnswer(text, language, recentContext, lease)
}

function ensureOptimizedHumanLikeText(
  text: string,
  language: VoiceLanguage,
  purpose: OptimizedContext['purpose'],
): string {
  if (!text) return text
  if (/^(?:啊|哦|嗯|好嘞|好的|好，|行，|ah|oh|mm|right|well|all right|there we go)/i.test(text)) {
    return text
  }
  const failed = /没有完成|未完成|失败|did not complete|failed/i.test(text)
  if (failed) {
    return language === 'zh'
      ? `嗯，后台刚才眨了一下眼睛。${text} 我可以再试一次。`
      : `Mm, the backend blinked for a moment. ${text} I can try again.`
  }
  if (purpose === 'background_action_accepted') {
    return language === 'zh'
      ? `好嘞，交给我。${text}`
      : `Right, leave it with me. ${text}`
  }
  return language === 'zh' ? `啊，搞定了。${text}` : `Ah, there we go. ${text}`
}
