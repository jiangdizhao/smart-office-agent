import {
  INTERACTION_PANEL_CLOSE_EVENT,
  INTERACTION_PANEL_OPEN_EVENT,
} from '../display/multiScreenWindowManager'
import { publishSessionMessage } from '../interaction/sessionEventBus'
import { realtimeAgent, type VoiceLanguage } from '../voice/realtimeAgentRuntime'
import { voiceOutputManager } from '../voice/voiceOutputManager'
import { visitLeaseRegistry, type VisitLease } from '../vision/visitLeaseRegistry'
import {
  endSalesVisit,
  requestSalesProactive,
} from './salesConversationClient'
import { renderSalesReply } from './salesReplyRenderer'

const CONVERSATION_STORAGE_KEY = 'smartoffice_voice_conversation_id'
const FIRST_NUDGE_MS = 7_000
const SECOND_NUDGE_MS = 15_000
const CJK = /[\u3400-\u9fff]/

type ActiveSchedule = {
  generation: number
  visitId: string
  conversationId: string
  language: VoiceLanguage
  quietStartedAt: number
  lease: VisitLease
}

class SalesProactiveScheduler {
  private installed = false
  private generation = 0
  private timers = new Set<number>()
  private active: ActiveSchedule | null = null
  private userSpeaking = false
  private interactionInputActive = false
  private lastLanguage: VoiceLanguage = 'zh'

  install(): void {
    if (this.installed) return
    this.installed = true

    window.addEventListener('smartoffice:visit-activated', this.onVisitActivated)
    window.addEventListener('smartoffice:visit-revoked', this.onVisitRevoked)
    window.addEventListener('smartoffice:realtime-vad-speech-started', this.onUserSpeechStarted)
    window.addEventListener('smartoffice:continuous-user-transcript', this.onUserTranscript)
    window.addEventListener('smartoffice:realtime-continuous-utterance', this.onUserTranscript)
    window.addEventListener('smartoffice:realtime-speaking-stop', this.onAgentSpeakingStopped)
    window.addEventListener('smartoffice:direct-assistant-caption', this.onAssistantCaption)
    window.addEventListener(INTERACTION_PANEL_OPEN_EVENT, this.onInteractionOpen)
    window.addEventListener(INTERACTION_PANEL_CLOSE_EVENT, this.onInteractionClose)
  }

  private conversationId(): string {
    return sessionStorage.getItem(CONVERSATION_STORAGE_KEY)?.trim() ?? ''
  }

  private inferLanguage(text: string): VoiceLanguage {
    const clean = text.trim()
    if (CJK.test(clean)) return 'zh'
    if (/[A-Za-z]/.test(clean)) return 'en'
    return this.lastLanguage
  }

  private clearTimers(): void {
    for (const timer of this.timers) window.clearTimeout(timer)
    this.timers.clear()
  }

  cancel(reason: string): void {
    this.generation += 1
    this.clearTimers()
    this.active = null
    console.info('[SalesRuntime] proactive-schedule-cancelled', { reason })
  }

  private scheduleFromQuietBoundary(reason: string): void {
    const lease = visitLeaseRegistry.current()
    const conversationId = this.conversationId()
    if (!lease || !conversationId || lease.signal.aborted || this.interactionInputActive) return

    this.generation += 1
    this.clearTimers()
    const generation = this.generation
    this.active = {
      generation,
      visitId: lease.visitId,
      conversationId,
      language: this.lastLanguage,
      quietStartedAt: Date.now(),
      lease,
    }
    this.arm(generation, FIRST_NUDGE_MS)
    this.arm(generation, SECOND_NUDGE_MS)
    console.info('[SalesRuntime] proactive-schedule-armed', {
      reason,
      visitId: lease.visitId,
      firstSeconds: FIRST_NUDGE_MS / 1_000,
      secondSeconds: SECOND_NUDGE_MS / 1_000,
    })
  }

  private arm(generation: number, delayMs: number): void {
    const timer = window.setTimeout(() => {
      this.timers.delete(timer)
      void this.fire(generation, delayMs)
    }, delayMs)
    this.timers.add(timer)
  }

  private async fire(generation: number, nominalDelayMs: number): Promise<void> {
    const active = this.active
    if (!active || active.generation !== generation || generation !== this.generation) return
    if (!visitLeaseRegistry.isCurrent(active.lease) || active.lease.signal.aborted) return

    const runtime = realtimeAgent.status()
    const agentSpeaking = runtime.outputActive || runtime.responseActive
    const toolActive = document.body.classList.contains('smartoffice-tool-active')
    if (this.userSpeaking || agentSpeaking || toolActive || this.interactionInputActive) {
      // The quiet interval starts again only after the blocking activity ends.
      if (agentSpeaking || this.userSpeaking) return
      this.arm(generation, 1_000)
      return
    }

    const silenceSeconds = Math.max(
      Math.round(nominalDelayMs / 1_000),
      Math.floor((Date.now() - active.quietStartedAt) / 1_000),
    )
    try {
      const response = await requestSalesProactive({
        conversationId: active.conversationId,
        visitId: active.visitId,
        language: active.language,
        silenceSeconds,
        userSpeaking: this.userSpeaking,
        agentSpeaking,
        toolActive,
        interactionInputActive: this.interactionInputActive,
        lease: active.lease,
      })
      if (
        !response.speak
        || generation !== this.generation
        || !visitLeaseRegistry.isCurrent(active.lease)
      ) return

      const text = await renderSalesReply({
        userText: '',
        language: active.language,
        plan: response.reply_plan,
        fallbackText: response.fallback_text,
        lease: active.lease,
      })
      if (generation !== this.generation || !visitLeaseRegistry.isCurrent(active.lease)) return

      publishSessionMessage({
        conversationId: active.conversationId,
        visitId: active.visitId,
        role: 'assistant',
        text,
        source: 'phase1_proactive_sales',
      })
      window.dispatchEvent(new CustomEvent('smartoffice:direct-assistant-caption', {
        detail: { text, source: 'phase1_proactive_sales' },
      }))
      await voiceOutputManager.speak(text, active.language, {
        lease: active.lease,
        signal: active.lease.signal,
        allowLocalFallback: false,
      })
    } catch (error) {
      if (!active.lease.signal.aborted) {
        console.error('[SalesRuntime] proactive-sales-failed-open', {
          message: error instanceof Error ? error.message : String(error),
          visitId: active.visitId,
          silenceSeconds,
        })
      }
    }
  }

  private onVisitActivated = (event: Event): void => {
    const detail = event instanceof CustomEvent ? event.detail : null
    const replacedVisitId = String(detail?.replacedVisitId ?? '').trim()
    const conversationId = this.conversationId()
    if (replacedVisitId && conversationId) {
      void endSalesVisit({ conversationId, visitId: replacedVisitId })
    }
    this.userSpeaking = false
    this.interactionInputActive = false
    this.cancel('visit_activated')
  }

  private onVisitRevoked = (event: Event): void => {
    const detail = event instanceof CustomEvent ? event.detail : null
    const revokedVisitId = String(
      detail?.visitId
      ?? this.active?.visitId
      ?? visitLeaseRegistry.current()?.visitId
      ?? '',
    ).trim()
    const conversationId = this.active?.conversationId || this.conversationId()
    if (revokedVisitId && conversationId) {
      void endSalesVisit({ conversationId, visitId: revokedVisitId })
    }
    this.userSpeaking = false
    this.interactionInputActive = false
    this.cancel('visit_revoked')
  }

  private onUserSpeechStarted = (): void => {
    this.userSpeaking = true
    this.cancel('visitor_speech_started')
  }

  private onUserTranscript = (event: Event): void => {
    const detail = event instanceof CustomEvent ? event.detail : null
    const text = String(detail?.transcript ?? detail?.text ?? '').trim()
    if (text) this.lastLanguage = this.inferLanguage(text)
    this.userSpeaking = false
    this.cancel('visitor_utterance_ready')
  }

  private onAssistantCaption = (event: Event): void => {
    const detail = event instanceof CustomEvent ? event.detail : null
    const text = String(detail?.text ?? '').trim()
    if (text) this.lastLanguage = this.inferLanguage(text)
  }

  private onAgentSpeakingStopped = (): void => {
    this.userSpeaking = false
    this.scheduleFromQuietBoundary('agent_output_completed')
  }

  private onInteractionOpen = (): void => {
    this.interactionInputActive = true
    this.cancel('interaction_panel_open')
  }

  private onInteractionClose = (): void => {
    this.interactionInputActive = false
    this.scheduleFromQuietBoundary('interaction_panel_closed')
  }
}

export const salesProactiveScheduler = new SalesProactiveScheduler()

export function installSalesProactiveScheduler(): void {
  salesProactiveScheduler.install()
}
