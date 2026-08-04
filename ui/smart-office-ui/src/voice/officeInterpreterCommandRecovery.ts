import {
  commandClarification,
  recoverCommandTranscript,
} from './commandTranscriptRepair'
import { deterministicPresentationSteps } from './presentationCommandPlan'
import {
  realtimeOfficeInterpreter,
  type RealtimeOfficeDecision,
} from './realtimeOfficeInterpreter'
import type { VoiceLanguage } from './realtimeAgentRuntime'

let installed = false

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

    // A complete domain intent is resolved before generic bare-application
    // clarification. Rich compound requests remain intact for the model planner.
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

    return await originalInterpret(recovered.normalized, language)
  }
}

installOfficeInterpreterCommandRecovery()
