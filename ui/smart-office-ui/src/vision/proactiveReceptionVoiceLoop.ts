import { publishSessionMessage } from '../interaction/sessionEventBus'
import { preemptiveTurnCoordinator } from '../voice/preemptiveTurnCoordinator'
import type { OfficeVoiceController } from '../voice/useOfficeVoiceController'
import { realtimeAgent } from '../voice/realtimeAgentRuntime'
import { visitLeaseRegistry } from './visitLeaseRegistry'

export type AutomaticVoiceTurnResult =
  | { kind: 'heard'; transcript: string }
  | { kind: 'silence' }
  | { kind: 'aborted' }
  | { kind: 'error'; message: string }

const DUPLICATE_TRANSCRIPT_WINDOW_MS = 1_800
const recentTranscripts = new Map<string, number>()

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function transcriptFingerprint(visitId: string | null, text: string): string {
  const normalized = text
    .normalize('NFKC')
    .toLocaleLowerCase()
    .replace(/[\u200b\ufeff]/g, '')
    .replace(/[，。！？、;；:：,.!?\s]+/g, '')
    .trim()
  return `${visitId ?? 'no-visit'}:${normalized}`
}

function isDuplicateTranscript(visitId: string | null, text: string): boolean {
  const now = performance.now()
  for (const [key, seenAt] of recentTranscripts) {
    if (now - seenAt > DUPLICATE_TRANSCRIPT_WINDOW_MS * 2) recentTranscripts.delete(key)
  }
  const key = transcriptFingerprint(visitId, text)
  const seenAt = recentTranscripts.get(key)
  recentTranscripts.set(key, now)
  return seenAt !== undefined && now - seenAt <= DUPLICATE_TRANSCRIPT_WINDOW_MS
}

async function continueAfterSupersededTurn(
  transcript: string,
  reason: string,
): Promise<AutomaticVoiceTurnResult> {
  await preemptiveTurnCoordinator.recoverToReady(reason)
  console.info('[PreemptiveTurn] superseded-turn-continues-listening', {
    reason,
    transcriptLength: transcript.length,
  })
  return { kind: 'heard', transcript }
}

export async function captureAutomaticRealtimeTurn(
  controller: () => OfficeVoiceController,
  visitSignal: AbortSignal,
): Promise<AutomaticVoiceTurnResult> {
  if (visitSignal.aborted) return { kind: 'aborted' }
  preemptiveTurnCoordinator.attachController(controller)
  let turnEpoch: number | null = null
  let turnSignal: AbortSignal = visitSignal
  let transcript = ''

  try {
    const initialController = controller()
    await realtimeAgent.startContinuousCapture(initialController.language, visitSignal)
    const firstTranscript = await realtimeAgent.nextContinuousUtterance(visitSignal)
    const latestTranscript = preemptiveTurnCoordinator.takeLatestUtterance(firstTranscript)
    if (visitSignal.aborted) return { kind: 'aborted' }

    const turn = preemptiveTurnCoordinator.beginTurn(visitSignal)
    turnEpoch = turn.epoch
    turnSignal = turn.signal
    transcript = latestTranscript.trim()
    if (!transcript || transcript === '__UNCLEAR__') return { kind: 'silence' }

    const lease = visitLeaseRegistry.current()
    const visitId = lease?.visitId ?? null
    if (isDuplicateTranscript(visitId, transcript)) {
      console.warn('[UnifiedVoiceRoute] duplicate-transcript-suppressed', {
        visitId,
        transcriptLength: transcript.length,
        duplicateWindowMs: DUPLICATE_TRANSCRIPT_WINDOW_MS,
      })
      window.dispatchEvent(new CustomEvent('smartoffice:duplicate-utterance-suppressed', {
        detail: { visitId, transcript, duplicateWindowMs: DUPLICATE_TRANSCRIPT_WINDOW_MS },
      }))
      return { kind: 'heard', transcript }
    }

    publishSessionMessage({
      conversationId: initialController.conversationId,
      visitId,
      role: 'user',
      text: transcript,
      source: 'continuous_realtime_unified_route',
    })
    window.dispatchEvent(new CustomEvent('smartoffice:continuous-user-transcript', {
      detail: {
        transcript,
        rawTranscript: latestTranscript,
        commandRecovered: latestTranscript !== firstTranscript,
        turnEpoch,
        routeOwner: 'unified_semantic_router',
      },
    }))

    await preemptiveTurnCoordinator.waitForCancellation()
    if (!preemptiveTurnCoordinator.isCurrent(turnEpoch)) {
      return await continueAfterSupersededTurn(
        transcript,
        'unified_turn_superseded_before_submit',
      )
    }

    // No desktop, interaction or sales action is allowed before this call. The
    // shared Controller owns the single route through Unified Semantic Router,
    // deterministic policy, the domain planner and the idempotent executor.
    // Capture one current snapshot so the turn cannot mix controller instances.
    const current = controller()
    await current.submit(transcript, 'voice')
    if (!preemptiveTurnCoordinator.isCurrent(turnEpoch)) {
      return await continueAfterSupersededTurn(
        transcript,
        'unified_turn_superseded_after_submit',
      )
    }
    return { kind: 'heard', transcript }
  } catch (error) {
    if (visitSignal.aborted) {
      await realtimeAgent.stopContinuousCapture(true).catch(() => undefined)
      return { kind: 'aborted' }
    }

    const aborted = error instanceof Error && error.name === 'AbortError'
    if (aborted || turnSignal.aborted) {
      return await continueAfterSupersededTurn(transcript, 'superseded_unified_turn')
    }

    const livenessTimeout = error instanceof Error && error.name === 'UtteranceLivenessError'
    if (livenessTimeout) {
      await preemptiveTurnCoordinator.recoverToReady('utterance_liveness_timeout')
      return {
        kind: 'error',
        message: 'The current speech segment did not end cleanly; listening has been recovered.',
      }
    }

    const recovered = await preemptiveTurnCoordinator.recoverToReady('unified_turn_error')
    return {
      kind: 'error',
      message: recovered
        ? `${errorText(error)} The controller recovered and listening will continue.`
        : errorText(error),
    }
  } finally {
    if (turnEpoch !== null) preemptiveTurnCoordinator.finishTurn(turnEpoch)
  }
}
