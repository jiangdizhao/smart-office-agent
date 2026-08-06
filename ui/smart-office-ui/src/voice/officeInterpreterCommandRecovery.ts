import './officeTurnWatchdog'
import {
  commandClarification,
  recoverCommandTranscript,
} from './commandTranscriptRepair'
import {
  deterministicPresentationSteps,
  mentionsPresentation,
  presentationClarification,
} from './presentationCommandPlan'
import {
  realtimeOfficeInterpreter,
  type RealtimeOfficeDecision,
} from './realtimeOfficeInterpreter'
import type { VoiceLanguage } from './realtimeAgentRuntime'

let installed = false

const OFFICE_DOMAIN = /teams|one\s*note|onenote|outlook|word|excel|邮件|邮箱|草稿|会议|音量|亮度|音乐|录音|文档|系统设置|office/i
const OFFICE_ACTION = /打开|关闭|启动|停止|创建|生成|发送|调整|设置|播放|执行|总结|整理|查看|读取|预约|操作|open|close|launch|start|stop|create|generate|send|adjust|set|play|execute|summari[sz]e|review|read|book/i

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
    const raw = recovered.raw || text

    const presentationSteps = deterministicPresentationSteps(raw)
      ?? deterministicPresentationSteps(recovered.normalized)
    if (presentationSteps) {
      return deterministicDecision(
        presentationSteps,
        recovered.raw,
        recovered.normalized,
      )
    }
    if (mentionsPresentation(raw) || mentionsPresentation(recovered.normalized)) {
      return {
        kind: 'clarify',
        clarification: presentationClarification(language),
      }
    }

    const clarification = commandClarification(recovered)
    if (clarification) {
      return { kind: 'clarify', clarification }
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

    const decision = await originalInterpret(recovered.normalized, language)
    if (
      decision.kind === 'none'
      && OFFICE_DOMAIN.test(recovered.normalized)
      && OFFICE_ACTION.test(recovered.normalized)
    ) {
      return {
        kind: 'clarify',
        clarification: language === 'zh'
          ? '请明确要执行的办公操作，例如打开应用、调整音量、处理邮件，或操作 PPT。'
          : 'Please state the Office action clearly, such as opening an app, changing volume, handling email, or controlling PowerPoint.',
      }
    }
    return decision
  }
}

installOfficeInterpreterCommandRecovery()
