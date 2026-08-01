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

    if (
      recovered.target === 'powerpoint'
      && (recovered.action === 'open' || recovered.action === 'close')
    ) {
      const actionName = recovered.action === 'open'
        ? 'presentation_open_configured'
        : 'presentation_close'
      console.info('[OfficePlan]', {
        source: 'deterministic_recovered_powerpoint_command',
        rawTranscript: recovered.raw,
        normalizedTranscript: recovered.normalized,
        actionName,
      })
      return {
        kind: 'tool_call',
        toolCall: {
          name: 'office_plan',
          arguments: {
            steps: [{ name: actionName }],
          },
          call_id: null,
          source: 'gpt_realtime',
        },
      }
    }

    return await originalInterpret(recovered.normalized, language)
  }
}

installOfficeInterpreterCommandRecovery()
