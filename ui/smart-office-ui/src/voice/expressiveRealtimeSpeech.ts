import { realtimeAgent, type VoiceLanguage } from './realtimeAgentRuntime'
import type { VoiceDeliveryPlan } from '../sales/voiceDelivery'
import { deliveryInstruction } from '../sales/voiceDelivery'

type RealtimeInternals = {
  language: VoiceLanguage
  generation: number
  stopOutput: () => Promise<void>
  ensureConnected: (generation: number, signal?: AbortSignal) => Promise<void>
  assertGeneration: (generation: number) => void
  createResponse: (
    modalities: Array<'text' | 'audio'>,
    instructions: string,
    purpose: string,
    sourceText: string,
    generation: number,
    signal?: AbortSignal,
  ) => Promise<string>
}

/**
 * Uses the same persistent Realtime session as speakExact, but supplies a
 * controlled delivery instruction. The final text remains exact; delivery never
 * gains permission to add or paraphrase content.
 */
export async function speakExpressiveExact(
  text: string,
  language: VoiceLanguage,
  delivery: VoiceDeliveryPlan,
  signal?: AbortSignal,
): Promise<string> {
  const clean = text.trim()
  if (!clean) return ''
  const agent = realtimeAgent as unknown as RealtimeInternals
  agent.language = language
  const generation = agent.generation
  await agent.stopOutput()
  await agent.ensureConnected(generation, signal)
  agent.assertGeneration(generation)
  const style = deliveryInstruction(delivery, language)
  const instruction = language === 'en'
    ? `Read the final answer below exactly. ${style}\nFINAL ANSWER:\n${clean}`
    : `请逐字朗读下面的最终答复。${style}\n最终答复：\n${clean}`
  const spoken = await agent.createResponse(
    ['audio'],
    instruction,
    'controlled_expressive_exact_answer',
    clean,
    generation,
    signal,
  )
  agent.assertGeneration(generation)
  return spoken || clean
}
