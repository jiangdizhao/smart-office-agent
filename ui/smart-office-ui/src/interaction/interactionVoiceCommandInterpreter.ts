import {
  currentInteractionPanel,
  matchInteractionWindowIntent,
  type InteractionWindowKind,
} from '../display/multiScreenWindowManager'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import {
  type InteractionVoiceCommand,
} from './interactionPanelCommandBus'
import { resolveSemanticInteractionIntent } from './semanticInteractionInterpreter'

function normalizeCommandText(text: string): string {
  return text
    .normalize('NFKC')
    .toLocaleLowerCase()
    .replace(/[，。！？、;；:：,.!?]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function isDirectMeetingRequest(clean: string): boolean {
  if (/(?:取消预约|取消会议|查看预约|预约记录|历史预约)/i.test(clean)) return false
  return (
    /^(?:请|帮我|麻烦|我想|我要|希望|需要|可以|能不能|给我)?(?:打开|显示|进入|安排|预约|预订|约)?(?:一下|一个|一次|个)?(?:预约会议|会议预约|预约日历|会议日历|产品演示预约|演示预约|产品演示|会议)(?:功能|界面|页面|窗口)?$/i.test(clean)
    || /^(?:预约|预订|约)(?:一下|一个|一次|个)?(?:会议|时间|产品演示)$/i.test(clean)
    || /^(?:帮我|请|麻烦)?(?:约个时间|安排个时间|安排一次会面|安排一次会议|安排产品演示)$/i.test(clean)
    || /(?:我想|我要|希望|需要|帮我|请|可以|能不能).{0,10}(?:预约|预订|约|安排).{0,12}(?:会议|会面|时间|产品演示)/i.test(clean)
    || /(?:什么时候|哪天|哪个时间).{0,12}(?:可以见面|能开会|方便会面|有时间)/i.test(clean)
    || /\b(?:book|schedule|arrange|open|show)\b.{0,28}\b(?:meeting|appointment|demo session|calendar)\b/i.test(clean)
  )
}

function isDirectContactFormRequest(clean: string): boolean {
  return (
    /^(?:打开|显示|调出|进入|填写|我要填写|我想填写|我想打开|请打开|帮我打开).{0,8}(?:登记信息表|登记表|个人信息表|联系信息表|访客登记表|登记信息|个人信息|联系信息|联系方式)(?:窗口|表单|页面)?$/i.test(clean)
    || /^(?:登记信息表|登记表|个人信息表|联系信息表|访客登记表)$/i.test(clean)
    || /\b(?:open|show|display|fill(?: in)?|complete)\b.{0,24}\b(?:registration form|visitor form|contact form|personal information form)\b/i.test(clean)
  )
}

function isDirectTranscriptPanelRequest(clean: string): boolean {
  if (/结果中心|保存的|历史|已保存|列表/i.test(clean)) return false
  return (
    /^(?:打开|显示|调出|进入|查看|看看|我要看|我想看|请打开|帮我打开).{0,8}(?:对话记录|对话总结|聊天记录|聊天总结|会话记录|会话总结|当前对话|当前聊天|session要点)(?:窗口|页面)?$/i.test(clean)
    || /^(?:对话记录|对话总结|聊天记录|聊天总结|会话记录|会话总结)$/i.test(clean)
    || /\b(?:open|show|display|view)\b.{0,24}\b(?:current summary|session summary|current conversation|chat history|conversation history)\b/i.test(clean)
  )
}

function hasProtectedResultsContext(clean: string, active: string | null): boolean {
  return active === 'results' || /结果中心|已登记|登记结果|保存的|已保存|历史|列表|记录库|客户资料库|联系人记录|收集的结果|访客档案/i.test(clean)
}

function isGenericPanelCloseRequest(clean: string): boolean {
  return /^(?:关闭|关掉|退出|收起|取消|close|dismiss|exit)(?:一下)?(?:当前|这个|该)?(?:界面|窗口|面板)?(?:一下)?$/i.test(clean)
}

function localCommand(text: string): InteractionVoiceCommand | null {
  const clean = normalizeCommandText(text)
  if (!clean) return null
  const active = currentInteractionPanel()?.kind ?? null
  const close = /(?:关闭|关掉|退出|收起|取消|close|dismiss|exit)/i.test(clean)

  if (
    /(?:停止|结束).{0,8}(?:并|然后)?(?:保存)?.{0,8}(?:总结|概括|整理).{0,8}(?:录音|对话)|(?:停止|结束).{0,8}(?:录音|对话).{0,8}(?:总结|概括|整理)|(?:stop|finish).{0,20}(?:recording).{0,20}(?:summari[sz]e|recap)/i.test(clean)
  ) return { target: 'recording', action: 'stop_save_summarize' }

  if (
    /(?:总结|概括|整理|回顾).{0,12}(?:录音|现场对话|刚才的谈话|人员对话)|(?:录音|现场对话|刚才的谈话).{0,12}(?:总结|概括|整理|回顾)|(?:summari[sz]e|recap).{0,24}(?:recording|conversation|discussion)/i.test(clean)
  ) return { target: 'recording', action: 'summarize' }

  if (
    /(?:停止|结束|完成).{0,8}(?:录音|录制)|(?:录音|录制).{0,8}(?:停止|结束|完成)|(?:stop|finish|end).{0,20}(?:recording)/i.test(clean)
  ) return { target: 'recording', action: 'stop_save' }

  if (
    /(?:开始|启动|现在开始|请开始).{0,8}(?:录音|录制)|(?:录音|录制).{0,8}(?:开始|启动)|(?:start|begin).{0,20}(?:recording)/i.test(clean)
  ) return { target: 'recording', action: 'start' }

  if (
    /(?:下载|保存到本地).{0,8}(?:录音|音频)|(?:download).{0,20}(?:recording|audio)/i.test(clean)
  ) return { target: 'recording', action: 'download' }

  if (close && /(?:录音界面|录音窗口|实时录音)/i.test(clean)) {
    return { target: 'recording', action: 'close' }
  }
  if (close && /(?:预约会议|会议预约|会议日历|预约日历)/i.test(clean)) {
    return { target: 'meeting', action: 'close' }
  }
  if (close && /(?:对话记录|对话总结|聊天记录|聊天总结|会话记录|会话总结)/i.test(clean)) {
    return { target: 'transcript', action: 'close' }
  }
  if (close && /(?:结果中心)/i.test(clean)) {
    return { target: 'results', action: 'close' }
  }
  if (close && /(?:登记信息|登记表|个人信息表|联系表)/i.test(clean)) {
    return { target: 'contact', action: 'close' }
  }

  // Meeting booking is handled before every semantic or general-chat route. This
  // prevents intermittent "feature unavailable" answers when ASR adds filler words.
  if (isDirectMeetingRequest(clean)) {
    return { target: 'meeting', action: 'open' }
  }

  if (isDirectContactFormRequest(clean)) {
    return { target: 'contact', action: 'open' }
  }
  if (isDirectTranscriptPanelRequest(clean)) {
    return { target: 'transcript', action: 'open' }
  }

  const protectedContext = hasProtectedResultsContext(clean, active)

  if (
    protectedContext
    && /(?:显示|查看|切换到|打开).{0,8}(?:已登记信息|联系人(?:列表|记录)?|客户资料(?:列表|记录)?|登记结果)|(?:show|view).{0,20}(?:contacts|contact records|registered visitors)/i.test(clean)
  ) return { target: 'results', action: 'show_contacts' }

  if (
    protectedContext
    && /(?:显示|查看|切换到|打开).{0,8}(?:录音文件|录音列表|已保存录音)|(?:show|view).{0,20}(?:recordings|recording list|saved recordings)/i.test(clean)
  ) return { target: 'results', action: 'show_recordings' }

  if (
    protectedContext
    && /(?:结果中心).{0,8}(?:当前对话|对话记录|对话总结)|(?:切换到|显示|查看).{0,8}(?:结果中心里的|已保存的)?(?:当前对话|对话记录|对话总结)|(?:show|view).{0,20}(?:summary|transcript).{0,12}(?:result center)?/i.test(clean)
  ) return { target: 'results', action: 'show_transcript' }

  if (
    /(?:导出|下载).{0,8}(?:csv|联系人(?:列表|记录)?|已登记信息)|(?:export|download).{0,20}(?:csv|contacts)/i.test(clean)
  ) return { target: 'results', action: 'export_csv' }

  if (
    /(?:播放|听一下|试听).{0,8}(?:最新|最近)?(?:录音)|(?:play).{0,20}(?:latest|most recent)?\s*(?:recording)/i.test(clean)
  ) return { target: 'results', action: 'play_latest_recording' }

  if (
    /(?:打开|显示).{0,8}(?:录音目录|保存目录|文件夹|资源管理器)|(?:open).{0,20}(?:recording folder|output folder|explorer)/i.test(clean)
  ) return { target: 'results', action: 'open_directory' }

  if (/^(?:刷新|重新加载|更新|refresh|reload)(?:一下)?$/i.test(clean)) {
    if (active === 'results') return { target: 'results', action: 'refresh' }
    if (active === 'transcript') return { target: 'transcript', action: 'refresh' }
  }

  if (active && isGenericPanelCloseRequest(clean)) {
    if (active === 'contact') return { target: 'contact', action: 'close' }
    if (active === 'meeting') return { target: 'meeting', action: 'close' }
    if (active === 'recording') return { target: 'recording', action: 'close' }
    if (active === 'transcript') return { target: 'transcript', action: 'close' }
    return { target: 'results', action: 'close' }
  }

  return null
}

function openCommand(kind: InteractionWindowKind): InteractionVoiceCommand {
  if (kind === 'contact') return { target: 'contact', action: 'open' }
  if (kind === 'meeting') return { target: 'meeting', action: 'open' }
  if (kind === 'recording') return { target: 'recording', action: 'open' }
  if (kind === 'transcript') return { target: 'transcript', action: 'open' }
  return { target: 'results', action: 'open' }
}

export type InteractionVoiceDecision = {
  command: InteractionVoiceCommand | null
  confidence: number
  source: 'local_action' | 'fast_open' | 'semantic_open' | 'none'
}

export async function resolveInteractionVoiceCommand(
  text: string,
  language: VoiceLanguage,
  signal?: AbortSignal,
): Promise<InteractionVoiceDecision> {
  const local = localCommand(text)
  if (local) return { command: local, confidence: 1, source: 'local_action' }

  const fast = matchInteractionWindowIntent(text)
  if (fast) return { command: openCommand(fast), confidence: 1, source: 'fast_open' }

  const semantic = await resolveSemanticInteractionIntent(text, language, signal)
  if (semantic.intent === 'none') {
    return { command: null, confidence: semantic.confidence, source: 'none' }
  }
  return {
    command: openCommand(semantic.intent),
    confidence: semantic.confidence,
    source: 'semantic_open',
  }
}
