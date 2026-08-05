import {
  INTERACTION_PANEL_CLOSE_EVENT,
  INTERACTION_PANEL_OPEN_EVENT,
  INTERACTION_PANEL_RESULT_EVENT,
  currentInteractionPanel,
  type InteractionPanelCommandResult,
  type InteractionWindowKind,
  type InteractionWindowRequest,
} from '../display/multiScreenWindowManager'
import { publishSessionMessage } from '../interaction/sessionEventBus'
import {
  clearSemanticPendingIntent,
  setSemanticPendingIntent,
} from '../routing/unifiedSemanticRouterClient'
import { realtimeAgent, type VoiceLanguage } from '../voice/realtimeAgentRuntime'
import {
  voiceOutputManager,
  type AssistantOutputLifecycleDetail,
} from '../voice/voiceOutputManager'
import { visitLeaseRegistry, type VisitLease } from '../vision/visitLeaseRegistry'
import { endSalesVisit } from './salesConversationClient'
import {
  endPhase2BVisit,
  observePhase2BTurn,
  reportPhase2BInteraction,
  reportPhase2BOutput,
  requestPhase2BProactive,
  type Phase2BInteractionKind,
  type Phase2BStatus,
} from './salesPhase2BClient'

const CONVERSATION_STORAGE_KEY = 'smartoffice_voice_conversation_id'
const DEFAULT_FIRST_NUDGE_MS = 8_000
const DEFAULT_SECOND_NUDGE_MS = 10_000
const DEFAULT_BUSY_RETRY_MS = 2_000
const DEFAULT_AFTER_INTERACTION_MS = 3_000
const CJK = /[\u3400-\u9fff]/
const TERMINAL_PURPOSE = /visit[_-]?end|farewell|goodbye|closing|sales_phase2b_retreat/i

export type Phase2BOrchestratorStatus = {
  phase: 'phase2b_engagement_conversion'
  runtimeState:
    | 'inactive'
    | 'awaiting_user'
    | 'user_speaking'
    | 'assistant_speaking'
    | 'tool_active'
    | 'interaction_active'
    | 'deferred_busy'
  visitId: string | null
  backendStage: string | null
  conversionState: string | null
  currentTopic: string | null
  currentIndustry: string | null
  purpose: string | null
  nextNudgeInMs: number | null
  totalNudgeCount: number | null
  lastReason: string | null
  interactionKind: Phase2BInteractionKind | null
}

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

type ClosedPanelDetail = {
  request?: InteractionWindowRequest | null
}

function interactionKind(value: InteractionWindowKind | null | undefined): Phase2BInteractionKind | null {
  return value ?? null
}

class SalesPhase2BEngagementOrchestrator {
  private installed = false
  private generation = 0
  private timer: number | null = null
  private active: ActiveSchedule | null = null
  private userSpeaking = false
  private interactionInputActive = false
  private lastLanguage: VoiceLanguage = 'zh'
  private lastReason: string | null = null
  private backendStatus: Phase2BStatus | null = null
  private runtimeState: Phase2BOrchestratorStatus['runtimeState'] = 'inactive'
  private lastObservedText = ''
  private lastObservedAt = 0
  private pendingSyncGeneration = 0
  private activeInteraction: InteractionWindowRequest | null = null
  private verifiedPanelInstances = new Set<string>()
  private closedPanelInstances = new Set<string>()

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
    window.addEventListener(INTERACTION_PANEL_CLOSE_EVENT, this.onInteractionCloseRequested)
    window.addEventListener(INTERACTION_PANEL_RESULT_EVENT, this.onInteractionResult)
    window.addEventListener('smartoffice:interaction-panel-closed', this.onInteractionClosed)
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

  private clearSchedule(reason: string, runtimeState: Phase2BOrchestratorStatus['runtimeState']): void {
    this.generation += 1
    this.clearTimer()
    this.active = null
    this.lastReason = reason
    this.runtimeState = runtimeState
    console.info('[SalesPhase2B] schedule-cleared', { reason, runtimeState })
    this.publishStatus()
  }

  private publishStatus(): void {
    const status = this.status()
    ;(window as Window & {
      __SMART_OFFICE_SALES_PHASE2B__?: Phase2BOrchestratorStatus
    }).__SMART_OFFICE_SALES_PHASE2B__ = status
    window.dispatchEvent(new CustomEvent('smartoffice:sales-phase2b-status', { detail: status }))
  }

  status(): Phase2BOrchestratorStatus {
    return {
      phase: 'phase2b_engagement_conversion',
      runtimeState: this.runtimeState,
      visitId: this.active?.visitId ?? visitLeaseRegistry.current()?.visitId ?? null,
      backendStage: this.backendStatus?.stage ?? null,
      conversionState: this.backendStatus?.conversion_state ?? null,
      currentTopic: this.backendStatus?.current_topic ?? null,
      currentIndustry: this.backendStatus?.current_industry ?? null,
      purpose: this.active?.purpose ?? null,
      nextNudgeInMs: this.active
        ? Math.max(0, this.active.armedAt + this.active.delayMs - Date.now())
        : null,
      totalNudgeCount: this.backendStatus?.total_nudge_count ?? null,
      lastReason: this.lastReason,
      interactionKind: interactionKind(this.activeInteraction?.kind),
    }
  }

  private updateBackend(status: Phase2BStatus | null | undefined): void {
    if (status) this.backendStatus = status
    this.publishStatus()
  }

  private timing(name: keyof Phase2BStatus['timing'], fallback: number): number {
    const seconds = Number(this.backendStatus?.timing?.[name])
    return Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1_000) : fallback
  }

  private arm(
    purpose: string,
    delayMs: number,
    lease: VisitLease | null = visitLeaseRegistry.current(),
  ): void {
    const conversationId = this.conversationId()
    if (
      !lease
      || !conversationId
      || lease.signal.aborted
      || !visitLeaseRegistry.isCurrent(lease)
      || this.interactionInputActive
    ) return
    this.generation += 1
    this.clearTimer()
    const generation = this.generation
    this.active = {
      generation,
      visitId: lease.visitId,
      conversationId,
      language: this.lastLanguage,
      purpose,
      delayMs,
      armedAt: Date.now(),
      lease,
    }
    this.runtimeState = 'awaiting_user'
    this.lastReason = `armed:${purpose}`
    this.timer = window.setTimeout(() => {
      this.timer = null
      void this.fire(generation)
    }, delayMs)
    console.info('[SalesPhase2B] schedule-armed', {
      visitId: lease.visitId,
      purpose,
      delayMs,
      backendStage: this.backendStatus?.stage ?? null,
    })
    this.publishStatus()
  }

  private rearmBusy(active: ActiveSchedule, delayMs: number, reason: string): void {
    if (!visitLeaseRegistry.isCurrent(active.lease) || active.lease.signal.aborted) return
    this.runtimeState = 'deferred_busy'
    this.lastReason = reason
    this.arm(active.purpose, delayMs, active.lease)
  }

  private async fire(generation: number): Promise<void> {
    const active = this.active
    if (!active || active.generation !== generation || generation !== this.generation) return
    if (!visitLeaseRegistry.isCurrent(active.lease) || active.lease.signal.aborted) return

    const realtime = realtimeAgent.status()
    const agentSpeaking = realtime.outputActive || realtime.responseActive
    const toolActive = document.body.classList.contains('smartoffice-tool-active')
    if (this.userSpeaking || agentSpeaking || toolActive || this.interactionInputActive) {
      this.rearmBusy(
        active,
        this.timing('busy_retry_seconds', DEFAULT_BUSY_RETRY_MS),
        this.userSpeaking
          ? 'visitor_speaking_deferred'
          : agentSpeaking
            ? 'assistant_speaking_deferred'
            : toolActive
              ? 'tool_active_deferred'
              : 'interaction_active_deferred',
      )
      return
    }

    try {
      const response = await requestPhase2BProactive({
        conversationId: active.conversationId,
        visitId: active.visitId,
        language: active.language,
        userSpeaking: this.userSpeaking,
        agentSpeaking,
        toolActive,
        interactionInputActive: this.interactionInputActive,
        lease: active.lease,
      })
      this.updateBackend(response.engagement)
      this.lastReason = response.reason
      if (response.defer) {
        this.rearmBusy(
          active,
          response.retry_after_ms ?? this.timing('busy_retry_seconds', DEFAULT_BUSY_RETRY_MS),
          response.reason,
        )
        return
      }
      if (!response.speak || !response.reply) {
        this.clearSchedule(`backend_declined:${response.reason}`, 'inactive')
        return
      }
      if (generation !== this.generation || !visitLeaseRegistry.isCurrent(active.lease)) return

      const reply = response.reply
      const text = reply.text.trim() || reply.fallback_text.trim()
      if (!text) {
        this.clearSchedule('empty_phase2b_reply', 'inactive')
        return
      }
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
      if (active.lease.signal.aborted) return
      console.error('[SalesPhase2B] proactive-output-failed', {
        visitId: active.visitId,
        message: error instanceof Error ? error.message : String(error),
      })
      this.rearmBusy(
        active,
        this.timing('busy_retry_seconds', DEFAULT_BUSY_RETRY_MS),
        'proactive_request_failed_retry',
      )
    }
  }

  private async clearPending(visitId: string, lease: VisitLease | null): Promise<void> {
    const conversationId = this.conversationId()
    if (!conversationId || !visitId) return
    await clearSemanticPendingIntent({ conversationId, visitId, lease }).catch(() => undefined)
  }

  private async syncPendingIntent(detail: AssistantOutputLifecycleDetail): Promise<void> {
    const conversationId = this.conversationId()
    const visitId = detail.visitId ?? visitLeaseRegistry.current()?.visitId ?? ''
    if (!conversationId || !visitId) return
    const generation = ++this.pendingSyncGeneration
    const currentLease = visitLeaseRegistry.current()
    const lease = currentLease?.visitId === visitId ? currentLease : null
    await this.clearPending(visitId, lease)
    if (generation !== this.pendingSyncGeneration) return
    const intentType = detail.expectUserResponse
      ? detail.questionField === 'booking'
        ? 'booking_offer'
        : detail.questionField === 'contact'
          ? 'contact_offer'
          : null
      : null
    if (!intentType) return
    await setSemanticPendingIntent({
      conversationId,
      visitId,
      intentType,
      sourceTurnId: detail.outputId,
      metadata: {
        purpose: detail.purpose,
        reply_mode: detail.replyMode,
        question_field: detail.questionField,
        phase: 'phase2b_engagement_conversion',
      },
      lease,
    }).catch((error) => {
      console.error('[SalesPhase2B] pending-intent-sync-failed', {
        visitId,
        intentType,
        message: error instanceof Error ? error.message : String(error),
      })
    })
  }

  private reportOutput(detail: AssistantOutputLifecycleDetail): void {
    const conversationId = this.conversationId()
    const currentLease = visitLeaseRegistry.current()
    const visitId = detail.visitId ?? currentLease?.visitId ?? ''
    if (!conversationId || !visitId || detail.result === 'started') return
    const lease = currentLease?.visitId === visitId ? currentLease : null
    void reportPhase2BOutput({
      conversationId,
      visitId,
      result: detail.result,
      purpose: detail.purpose,
      replyMode: detail.replyMode,
      expectUserResponse: detail.expectUserResponse,
      questionField: detail.questionField,
      text: detail.text,
      cancelReason: detail.error ?? this.lastReason,
      lease,
    }).then((response) => this.updateBackend(response.engagement)).catch((error) => {
      console.error('[SalesPhase2B] output-result-report-failed', {
        visitId,
        purpose: detail.purpose,
        message: error instanceof Error ? error.message : String(error),
      })
    })
  }

  private observeUserText(text: string): void {
    const clean = text.replace(/\s+/g, ' ').trim()
    if (!clean) return
    const now = Date.now()
    if (clean === this.lastObservedText && now - this.lastObservedAt < 1_500) return
    this.lastObservedText = clean
    this.lastObservedAt = now
    this.lastLanguage = this.inferLanguage(clean)
    const conversationId = this.conversationId()
    const lease = visitLeaseRegistry.current()
    if (!conversationId || !lease || lease.signal.aborted) return
    void observePhase2BTurn({
      conversationId,
      visitId: lease.visitId,
      language: this.lastLanguage,
      text: clean,
      lease,
    }).then((response) => this.updateBackend(response.engagement)).catch((error) => {
      if (lease.signal.aborted) return
      console.error('[SalesPhase2B] asynchronous-observer-failed-open', {
        visitId: lease.visitId,
        message: error instanceof Error ? error.message : String(error),
      })
    })
  }

  private reportInteraction(
    request: InteractionWindowRequest,
    result: 'opened' | 'submitted' | 'failed' | 'cancelled' | 'closed',
    verified: boolean,
    message = '',
    data: Record<string, unknown> = {},
  ): void {
    const conversationId = request.conversationId || this.conversationId()
    const visitId = String(request.visitId ?? visitLeaseRegistry.current()?.visitId ?? '').trim()
    const kind = interactionKind(request.kind)
    if (!conversationId || !visitId || !kind) return
    const lease = visitLeaseRegistry.current()
    void reportPhase2BInteraction({
      conversationId,
      visitId,
      kind,
      result,
      verified,
      message,
      data,
      lease: lease?.visitId === visitId ? lease : null,
    }).then((response) => this.updateBackend(response.engagement)).catch((error) => {
      console.error('[SalesPhase2B] interaction-result-report-failed', {
        visitId,
        kind,
        result,
        message: error instanceof Error ? error.message : String(error),
      })
    })
  }

  private finishInteraction(request: InteractionWindowRequest | null): void {
    if (!request) return
    const instanceId = String(request.panelInstanceId ?? '')
    if (instanceId && this.closedPanelInstances.has(instanceId)) return
    if (instanceId) this.closedPanelInstances.add(instanceId)
    const verified = Boolean(instanceId && this.verifiedPanelInstances.has(instanceId))
    if (!verified) this.reportInteraction(request, 'closed', false)
    this.activeInteraction = null
    this.interactionInputActive = false
    if (!verified) {
      this.arm(
        'interaction_closed_follow_up',
        this.timing('after_interaction_seconds', DEFAULT_AFTER_INTERACTION_MS),
      )
    } else {
      this.clearSchedule('verified_conversion_completed', 'inactive')
    }
  }

  private onVisitActivated = (event: Event): void => {
    const detail = event instanceof CustomEvent ? event.detail : null
    const replacedVisitId = String(detail?.replacedVisitId ?? '').trim()
    const conversationId = this.conversationId()
    if (replacedVisitId && conversationId) {
      void this.clearPending(replacedVisitId, null)
      void endSalesVisit({ conversationId, visitId: replacedVisitId })
      void endPhase2BVisit({ conversationId, visitId: replacedVisitId })
    }
    this.userSpeaking = false
    this.interactionInputActive = false
    this.activeInteraction = null
    this.backendStatus = null
    this.verifiedPanelInstances.clear()
    this.closedPanelInstances.clear()
    this.clearSchedule('visit_activated', 'inactive')
  }

  private onVisitRevoked = (event: Event): void => {
    const detail = event instanceof CustomEvent ? event.detail : null
    const visitId = String(
      detail?.visitId
      ?? this.active?.visitId
      ?? visitLeaseRegistry.current()?.visitId
      ?? '',
    ).trim()
    const conversationId = this.active?.conversationId || this.conversationId()
    if (visitId && conversationId) {
      void this.clearPending(visitId, null)
      void endSalesVisit({ conversationId, visitId })
      void endPhase2BVisit({ conversationId, visitId })
    }
    this.userSpeaking = false
    this.interactionInputActive = false
    this.activeInteraction = null
    this.backendStatus = null
    this.clearSchedule('visit_revoked', 'inactive')
  }

  private onUserSpeechStarted = (): void => {
    this.userSpeaking = true
    this.clearSchedule('visitor_speech_started', 'user_speaking')
  }

  private onUserTranscript = (event: Event): void => {
    const detail = event instanceof CustomEvent ? event.detail : null
    const text = String(detail?.transcript ?? detail?.text ?? '').trim()
    this.userSpeaking = false
    this.observeUserText(text)
    this.clearSchedule('visitor_utterance_ready', 'inactive')
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
    this.clearSchedule(`assistant_output_started:${detail.purpose}`, 'assistant_speaking')
  }

  private onAssistantOutputCompleted = (event: Event): void => {
    const detail = event instanceof CustomEvent
      ? event.detail as AssistantOutputLifecycleDetail
      : null
    if (!detail) return
    this.userSpeaking = false
    this.reportOutput(detail)
    void this.syncPendingIntent(detail)
    if (
      TERMINAL_PURPOSE.test(`${detail.purpose} ${detail.replyMode}`)
      || detail.questionField === 'booking'
      || detail.questionField === 'contact'
      || this.interactionInputActive
    ) {
      this.clearSchedule(`completed_without_idle_follow_up:${detail.purpose}`, 'inactive')
      return
    }
    const delay = detail.purpose === 'sales_phase2b_proactive_first'
      ? this.timing('second_idle_seconds', DEFAULT_SECOND_NUDGE_MS)
      : this.timing('first_idle_seconds', DEFAULT_FIRST_NUDGE_MS)
    // Any completed customer-facing output can resume the Visit-level engagement
    // episode. The Backend remains authoritative and can decline the next nudge.
    this.arm(detail.purpose, delay)
  }

  private onAssistantOutputInterrupted = (event: Event): void => {
    const detail = event instanceof CustomEvent
      ? event.detail as AssistantOutputLifecycleDetail
      : null
    if (!detail) return
    this.reportOutput(detail)
    this.clearSchedule(`assistant_output_interrupted:${detail.purpose}`, 'inactive')
  }

  private onAssistantOutputFailed = (event: Event): void => {
    const detail = event instanceof CustomEvent
      ? event.detail as AssistantOutputLifecycleDetail
      : null
    if (!detail) return
    this.reportOutput(detail)
    this.clearSchedule(`assistant_output_failed:${detail.purpose}`, 'inactive')
  }

  private onInteractionOpen = (event: Event): void => {
    const request = event instanceof CustomEvent
      ? event.detail as InteractionWindowRequest
      : null
    if (!request?.kind) return
    this.activeInteraction = request
    this.interactionInputActive = true
    this.clearSchedule('interaction_panel_open', 'interaction_active')
    this.reportInteraction(request, 'opened', true)
  }

  private onInteractionCloseRequested = (): void => {
    this.finishInteraction(this.activeInteraction ?? currentInteractionPanel())
  }

  private onInteractionClosed = (event: Event): void => {
    const detail = event instanceof CustomEvent ? event.detail as ClosedPanelDetail : null
    this.finishInteraction(detail?.request ?? this.activeInteraction)
  }

  private onInteractionResult = (event: Event): void => {
    const result = event instanceof CustomEvent
      ? event.detail as InteractionPanelCommandResult
      : null
    if (!result || !['submit_booking', 'submit_contact'].includes(result.action)) return
    const request = this.activeInteraction ?? currentInteractionPanel()
    if (!request) return
    const verified = result.ok && result.status === 'completed' && result.data?.verified === true
    if (verified && request.panelInstanceId) {
      this.verifiedPanelInstances.add(request.panelInstanceId)
    }
    this.reportInteraction(
      request,
      verified ? 'submitted' : 'failed',
      verified,
      result.message,
      result.data ?? {},
    )
    if (verified) this.clearSchedule(`verified_${result.action}`, 'interaction_active')
  }
}

export const salesPhase2BEngagementOrchestrator = new SalesPhase2BEngagementOrchestrator()

export function installSalesPhase2BEngagementOrchestrator(): void {
  salesPhase2BEngagementOrchestrator.install()
}
