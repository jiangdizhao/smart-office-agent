import {
  commandClarification,
  recoverCommandTranscript,
} from './commandSpeechRecovery'
import {
  realtimeOfficeInterpreter,
  type RealtimeOfficeDecision,
} from './realtimeOfficeInterpreter'
import type { VoiceLanguage } from './realtimeAgentRuntime'

let installed = false

const PRESENTATION_TERM = /(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿)/i
const PRESENTATION_QUESTION = /(?:是什么|什么是|怎么|如何|为什么|介绍|功能|用途|能做什么|有什么用|what\s+is|what\s+does|why|how\s+do|how\s+does|tell\s+me|explain|describe)/i
const START_SLIDESHOW = /(?:开始|启动|进入|全屏|播放).{0,8}(?:演示|放映|幻灯片|演示文稿|power\s*point|powerpoint|\bppt\b|slide\s*show|slideshow|presentation)|(?:演示|放映|幻灯片|演示文稿|power\s*point|powerpoint|\bppt\b|slide\s*show|slideshow|presentation).{0,8}(?:开始|启动|进入|全屏|播放)|\b(?:start|begin|run|play)\b.{0,16}\b(?:slide\s*show|slideshow|presentation)\b/i
const DEMONSTRATE_PRESENTATION = /(?:演示|展示|播放).{0,8}(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿)|(?:power\s*point|powerpoint|\bppt\b|幻灯片|演示文稿).{0,8}(?:演示|展示|播放)|\b(?:demonstrate|show|present)\b.{0,16}\b(?:power\s*point|powerpoint|ppt|slides?|presentation)\b/i
const NEXT_SLIDE = /(?:下一页|下一张|后一页|后一张|往后翻|向后翻|继续往下)|\b(?:next\s+slide|advance|move\s+forward)\b/i
const PREVIOUS_SLIDE = /(?:上一页|上一张|前一页|前一张|往前翻|向前翻|翻回前面)|\b(?:previous\s+slide|go\s+back|move\s+back(?:ward)?)\b/i
const END_SLIDESHOW = /(?:结束|停止|退出|关闭).{0,8}(?:演示|放映|幻灯片放映)|(?:演示|放映|幻灯片放映).{0,8}(?:结束|停止|退出|关闭)|\b(?:end|stop|exit)\b.{0,16}\b(?:slide\s*show|slideshow|presentation)\b/i
const PRESENTATION_STATUS = /(?:第几页|哪一页|当前页|现在是第几页|演示状态|放映状态)|\b(?:what|which)\s+slide\b|\bpresentation\s+status\b/i

function deterministicPresentationSteps(text: string): Array<Record<string, unknown>> | null {
  const clean = text.normalize('NFKC').replace(/\s+/g, ' ').trim()
  if (!clean || PRESENTATION_QUESTION.test(clean)) return null

  if (END_SLIDESHOW.test(clean)) {
    return [{ name: 'presentation_end_slideshow' }]
  }
  if (NEXT_SLIDE.test(clean)) {
    return [{ name: 'presentation_next_slide' }]
  }
  if (PREVIOUS_SLIDE.test(clean)) {
    return [{ name: 'presentation_previous_slide' }]
  }
  if (PRESENTATION_STATUS.test(clean)) {
    return [{ name: 'presentation_get_status' }]
  }
  if (START_SLIDESHOW.test(clean)) {
    return [{ name: 'presentation_start_slideshow' }]
  }
  if (DEMONSTRATE_PRESENTATION.test(clean) && PRESENTATION_TERM.test(clean)) {
    return [
      { name: 'presentation_open_configured' },
      { name: 'presentation_start_slideshow' },
    ]
  }
  return null
}

function deterministicDecision(
  steps: Array<Record<string, unknown>>,
  rawTranscript: string,
  normalizedTranscript: string,
): RealtimeOfficeDecision {
  const actionNames = steps.map((step) => String(step.name ?? 'unknown'))
  console.info('[OfficePlan] deterministic-presentation-command', {
    rawTranscript,
    normalizedTranscript,
    actionNames,
  })
  return {
    kind: 'tool_call',
    toolCall: {
      name: 'office_plan',
      arguments: { steps },
      call_id: null,
      // The Backend contract currently uses this value for all bounded Office
      // plans. The route reason and log identify this plan as deterministic.
      source: 'gpt_realtime',
    },
  }
}

export function installOfficeInterpreterCommandRecovery(): void {
  if (installed) return
  installed = true

  const originalInterpret = realtimeOfficeInterpreter.interpret.bind(
    realtimeOfficeInterpreter,
  )
  realtimeOfficeInterpreter.interpret = async (
    text: string,
    language: VoiceLanguage,
  ): Promise<RealtimeOfficeDecision> => {
    const recovered = recoverCommandTranscript(text, language)
    const clarification = commandClarification(recovered)
    if (clarification) {
      return { kind: 'clarify', clarification }
    }

    const presentationSteps = deterministicPresentationSteps(
      recovered.raw || text,
    ) ?? deterministicPresentationSteps(recovered.normalized)
    if (presentationSteps) {
      return deterministicDecision(
        presentationSteps,
        recovered.raw,
        recovered.normalized,
      )
    }

    if (
      recovered.target === 'powerpoint'
      && (recovered.action === 'open' || recovered.action === 'close')
    ) {
      const actionName = recovered.action === 'open'
        ? 'presentation_open_configured'
        : 'presentation_close'
      return deterministicDecision(
        [{ name: actionName }],
        recovered.raw,
        recovered.normalized,
      )
    }

    return await originalInterpret(recovered.normalized, language)
  }
}

installOfficeInterpreterCommandRecovery()
