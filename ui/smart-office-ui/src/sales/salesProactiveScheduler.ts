import {
  INTERACTION_PANEL_CLOSE_EVENT,
  INTERACTION_PANEL_OPEN_EVENT,
} from '../display/multiScreenWindowManager'
import { publishSessionMessage } from '../interaction/sessionEventBus'
import { realtimeAgent, type VoiceLanguage } from '../voice/realtimeAgentRuntime'
import {
  voiceOutputManager,
  type AssistantOutputLifecycleDetail,
} from '../voice/voiceOutputManager'
import { visitLeaseRegistry, type VisitLease } from '../vision/visitLeaseRegistry'
import { endSalesVisit } from './salesConversationClient'
import {
  reportSalesExperienceOutput,
  requestSalesExperienceProactive,
} from './salesExperienceClient'

const CONVERSATION_STORAGE_KEY = 'smartoffice_voice_conversation_id'
const FIRST_NUDGE_MS = 7_000
const SECOND_NUDGE_DELAY_MS = 8_000
const CJK = /[\u3400-\u9fff]/

type ActiveSchedule = {
  generation: number
  visitId: string
  conversationId: string
  language: VoiceLanguage
  purpose: string
  delayMs: number
  armedAt: number
  lease: VisitLease
}

export type SalesCoordinatorStatus = {
  state: 'inactive' | 'awaiting_user' | 'user_speaking' | 'assistant_speaking' | 'interaction_active'
  visitId: string | null
  purpose: string | null
  nextNudgeInMs: number | null
  lastCancelReason: string | null
  lastOutputResult: string | null
}

class SalesProactiveScheduler {
  private installed = false
  private generation = 0
  private timer: number | null = null
  private active: ActiveSchedule | null = null
  private userSpeaking = false
  private interactionInputActive = false
  private lastLanguage: VoiceLanguage = 'zh'
  private lastCancelReason: string | null = null
  private lastOutputResult: string | null = null
  private state: SalesCoordinatorStatus['state'] = 'inactive'

  install(): void {
    if (this.installed) return
    this.installed = true

    window.addEventListener('smartoffice:visit-activated', this.onVisitActivated)
    window.addEventListener('smartoffice:visit-revoked', this.onVisitRevoked)
    window.addEventListener('smartoffice:realtime-vad-speech-started', this.onUserSpeechStarted)
    window.addEventListener('smartoffice:continuous-user-transcript', this.onUserTranscript)
    window.addEventListener('smartoffice:realtime-continuous-utterance', this.onUserTranscript)
    window.addEventListener('smartoffice:assistant-output-started', this.onAssistantOutputStarted)
    window.addEventListener('smartoffice:assistant-output-completed', this.onAssistantOutputCompleted)
    window.addEventListener('smartoffice:assistant-output-interrupted', this.onAssistantOutputInterrupted)
    window.addEventListener('smartoffice:assistant-output-failed', this.onAssistantOutputFailed)
    window.addEventListener('smartoffice:direct-assistant-caption', this.onAssistantCaption)
    window.addEventListener(INTERACTION_PANEL_OPEN_EVENT, this.onInteractionOpen)
    window.addEventListener(INTERACTION_PANEL_CLOSE_EVENT, this.onInteractionClose)
    this.publishStatus()
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

  private clearTimer(): void {
    if (this.timer !== null) window.clearTimeout(this.timer)
    this.timer = null
  }

  private publishStatus(): void {
    const status = this.status()
    ;(window as Window & { __SMART_OFFICE_SALES_EXPERIENCE__?: SalesCoordinatorStatus })
      .__SMART_OFFICE_SALES_EXPERIENCE__ = status
    window.dispatchEvent(new CustomEvent('smartoffice:sales-experience-status', { detail: status }))
  }

  status(): SalesCoordinatorStatus {
    const nextNudgeInMs = this.active
      ? Math.max(0, this.active.armedAt + this.active.delayMs - Date.now())
      : null
    return {
      state: this.state,
      visitId: this.active?.visitId ?? visitLeaseRegistry.current()?.visitId ?? null,
      purpose: this.active?.purpose ?? null,
      nextNudgeInMs,
      lastCancelReason: this.lastCancelReason,
      lastOutputResult: this.lastOutputResult,
    }
  }

  cancel(reason: string, state: SalesCoordinatorStatus['state'] = 'inactive'): void {
    this.generation += 1
    this.clearTimer()
    this.active = null
    this.lastCancelReason = reason
    this.state = state
    console.info('[SalesExperience] quiet-schedule-cancelled', { reason })
    this.publishStatus()
  }

  private scheduleAfterCompletedOutput(
    detail: AssistantOutputLifecycleDetail,
    delayMs: number,
  ): void {
    const lease = visitLeaseRegistry.current()
    const conversationId = this.conversationId()
    if (
      !lease
      || !conversationId
      || lease.signal.aborted
      || this.interactionInputActive
      || detail.visitId !== lease.visitId
    ) return

    this.generation += 1
    this.clearTimer()
    const generation = this.generation
    this.active = {
      generation,
      visitId: lease.visitId,
      conversationId,
      language: this.lastLanguage,
      purpose: detail.purpose,
      delayMs,
      armedAt: Date.now(),
      lease,
    }
    this.state = 'awaiting_user'
    this.timer = window.setTimeout(() => {
      this.timer = null
      void this.fire(generation)
    }, delayMs)
    console.info('[SalesExperience] quiet-schedule-armed', {
      visitId: lease.visitId,
      afterPurpose: detail.purpose,
      delaySeconds: delayMs / 1_000,
      questionField: detail.questionField,
    })
    this.publishStatus()
  }

  private async fire(generation: number): Promise<void> {
    const active = this.active
    if (!active || active.generation !== generation || generation !== this.generation) return
    if (!visitLeaseRegistry.isCurrent(active.lease) || active.lease.signal.aborted) return

    const runtime = realtimeAgent.status()
    const agentSpeaking = runtime.outputActive || runtime.responseActive
    const toolActive = document.body.classList.contains('smartoffice-tool-active')
    if (this.userSpeaking || agentSpeaking || toolActive || this.interactionInputActive) {
      this.cancel(
        this.userSpeaking
          ? 'visitor_speaking_at_deadline'
          : agentSpeaking
            ? 'assistant_speaking_at_deadline'
            : toolActive
              ? 'tool_active_at_deadline'
              : 'interaction_active_at_deadline',
      )
      return
    }

    try {
      const response = await requestSalesExperienceProactive({
        conversationId: active.conversationId,
        visitId: active.visitId,
        language: active.language,
        userSpeaking: this.userSpeaking,
        agentSpeaking,
        toolActive,
        interactionInputActive: this.interactionInputActive,
        lease: active.lease,
      })
      if (
        !response.speak
        || !response.reply
        || generation !== this.generation
        || !visitLeaseRegistry.isCurrent(active.lease)
      ) return

      const reply = response.reply
      const text = reply.text.trim() || reply.fallback_text.trim()
      if (!text) return
      publishSessionMessage({
        conversationId: active.conversationId,
        visitId: active.visitId,
        role: 'assistant',
        text,
        source: reply.purpose,
      })
      window.dispatchEvent(new CustomEvent('smartoffice:direct-assistant-caption', {
        detail: { text, source: reply.purpose },
      }))
      await voiceOutputManager.speak(text, active.language, {
        lease: active.lease,
        signal: active.lease.signal,
        allowLocalFallback: false,
        delivery: reply.delivery,
        purpose: reply.purpose,
        replyMode: reply.reply_mode,
        expectUserResponse: reply.expect_user_response,
        questionField: reply.question_field,
      })
    } catch (error) {
      if (!active.lease.signal.aborted) {
        console.error('[SalesExperience] proactive-output-failed', {
          message: error instanceof Error ? error.message : String(error),
          visitId: active.visitId,
        })
      }
    }
  }

  private reportOutput(detail: AssistantOutputLifecycleDetail): void {
    const conversationId = this.conversationId()
    const lease = visitLeaseRegistry.current()
    if (!conversationId || !detail.visitId) return
    const result = detail.result === 'started' ? null : detail.result
    if (!result) return
    void reportSalesExperienceOutput({
      conversationId,
      visitId: detail.visitId,
      result,
      cancelReason: result === 'interrupted' || result === 'failed' ? this.lastCancelReason : null,
      lease: lease?.visitId === detail.visitId ? lease : null,
    }).catch(() => undefined)
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
    this.cancel('visitor_speech_started', 'user_speaking')
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

  private onAssistantOutputStarted = (event: Event): void => {
    const detail = event instanceof CustomEvent
      ? event.detail as AssistantOutputLifecycleDetail
      : null
    if (!detail) return
    this.lastLanguage = this.inferLanguage(detail.text)
    this.lastOutputResult = 'started'
    this.cancel(`assistant_output_started:${detail.purpose}`, 'assistant_speaking')
  }

  private onAssistantOutputCompleted = (event: Event): void => {
    const detail = event instanceof CustomEvent
      ? event.detail as AssistantOutputLifecycleDetail
      : null
    if (!detail) return
    this.userSpeaking = false
    this.lastOutputResult = 'completed'
    this.reportOutput(detail)
    if (!detail.expectUserResponse) {
      this.cancel(`completed_without_expected_reply:${detail.purpose}`)
      return
    }
    const delay = detail.purpose === 'sales_proactive_first'
      ? SECOND_NUDGE_DELAY_MS
      : FIRST_NUDGE_MS
    this.scheduleAfterCompletedOutput(detail, delay)
  }

  private onAssistantOutputInterrupted = (event: Event): void => {
    const detail = event instanceof CustomEvent
      ? event.detail as AssistantOutputLifecycleDetail
      : null
    if (!detail) return
    this.lastOutputResult = 'interrupted'
    this.reportOutput(detail)
    this.cancel(`assistant_output_interrupted:${detail.purpose}`)
  }

  private onAssistantOutputFailed = (event: Event): void => {
    const detail = event instanceof CustomEvent
      ? event.detail as AssistantOutputLifecycleDetail
      : null
    if (!detail) return
    this.lastOutputResult = 'failed'
    this.reportOutput(detail)
    this.cancel(`assistant_output_failed:${detail.purpose}`)
  }

  private onInteractionOpen = (): void => {
    this.interactionInputActive = true
    this.cancel('interaction_panel_open', 'interaction_active')
  }

  private onInteractionClose = (): void => {
    this.interactionInputActive = false
    this.cancel('interaction_panel_closed')
  }
}

export const salesProactiveScheduler = new SalesProactiveScheduler()

export function installSalesProactiveScheduler(): void {
  salesProactiveScheduler.install()
}
