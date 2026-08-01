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
      recovered.target === 'powerpoint' &&
      recovered.action === 'close'
    ) {
      console.info('[OfficePlan]', {
        source: 'deterministic_recovered_powerpoint_close',
        rawTranscript: recovered.raw,
        normalizedTranscript: recovered.normalized,
      })
      return {
        kind: 'tool_call',
        toolCall: {
          name: 'office_plan',
          arguments: {
            steps: [{ name: 'presentation_close' }],
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
