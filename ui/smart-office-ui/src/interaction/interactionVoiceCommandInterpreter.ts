import {
  currentInteractionPanel,
  matchInteractionWindowIntent,
} from '../display/multiScreenWindowManager'
import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'
import {
  type InteractionVoiceCommand,
} from './interactionPanelCommandBus'
import { resolveSemanticInteractionIntent } from './semanticInteractionInterpreter'

function localCommand(text: string): InteractionVoiceCommand | null {
  const clean = text.trim().toLocaleLowerCase()
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

  if (
    /(?:显示|查看|切换到|打开).{0,8}(?:登记信息|联系人|客户资料)|(?:show|view).{0,20}(?:contacts|contact records)/i.test(clean)
  ) return { target: 'results', action: 'show_contacts' }

  if (
    /(?:显示|查看|切换到|打开).{0,8}(?:录音文件|录音列表)|(?:show|view).{0,20}(?:recordings|recording list)/i.test(clean)
  ) return { target: 'results', action: 'show_recordings' }

  if (
    /(?:结果中心).{0,8}(?:当前对话|对话记录)|(?:切换到|显示|查看).{0,8}(?:结果中心里的)?(?:当前对话|对话记录)|(?:show|view).{0,20}(?:transcript).{0,12}(?:result center)?/i.test(clean)
  ) return { target: 'results', action: 'show_transcript' }

  if (
    /(?:导出|下载).{0,8}(?:csv|联系人|登记信息)|(?:export|download).{0,20}(?:csv|contacts)/i.test(clean)
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

  if (close && active) {
    if (active === 'contact') return { target: 'contact', action: 'close' }
    if (active === 'recording') return { target: 'recording', action: 'close' }
    if (active === 'transcript') return { target: 'transcript', action: 'close' }
    return { target: 'results', action: 'close' }
  }

  if (close && /(?:录音界面|录音窗口)/i.test(clean)) {
    return { target: 'recording', action: 'close' }
  }
  if (close && /(?:对话记录|聊天记录|会话记录)/i.test(clean)) {
    return { target: 'transcript', action: 'close' }
  }
  if (close && /(?:结果中心)/i.test(clean)) {
    return { target: 'results', action: 'close' }
  }
  if (close && /(?:登记信息|登记表|联系表)/i.test(clean)) {
    return { target: 'contact', action: 'close' }
  }

  return null
}

function openCommand(kind: 'contact' | 'recording' | 'transcript' | 'results'): InteractionVoiceCommand {
  if (kind === 'contact') return { target: 'contact', action: 'open' }
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
