import {
  visitLeaseRegistry,
  type VisitLease,
} from './visitLeaseRegistry'

export type VisitCandidate<TDetection> = {
  visitId: string
  detection: TDetection
  sourceEvent: string
}

export type VisitEndContext<TDetection> = {
  lease: VisitLease
  candidate: VisitCandidate<TDetection>
  reason: string
}

export type VisitOrchestratorHooks<TDetection> = {
  onPreempt: (
    ended: VisitEndContext<TDetection>,
    replacement: VisitCandidate<TDetection> | null,
  ) => void | Promise<void>
  onGreeting: (
    lease: VisitLease,
    candidate: VisitCandidate<TDetection>,
  ) => Promise<boolean>
  onConversation: (
    lease: VisitLease,
    candidate: VisitCandidate<TDetection>,
  ) => Promise<void>
  onFarewell: (
    ended: VisitEndContext<TDetection>,
    signal: AbortSignal,
  ) => Promise<void>
  onArchive: (ended: VisitEndContext<TDetection>) => Promise<void>
}

type ActiveVisit<TDetection> = {
  lease: VisitLease
  candidate: VisitCandidate<TDetection>
  greetingStarted: boolean
  greeted: boolean
}

export class VisitOrchestrator<TDetection> {
  private active: ActiveVisit<TDetection> | null = null
  private absenceTimer: number | null = null
  private farewellAbort: AbortController | null = null
  private disposed = false
  private readonly background = new Set<Promise<unknown>>()
  private readonly hooks: VisitOrchestratorHooks<TDetection>
  private readonly absenceGraceMs: number

  constructor(
    hooks: VisitOrchestratorHooks<TDetection>,
    absenceGraceMs = 2_000,
  ) {
    this.hooks = hooks
    this.absenceGraceMs = absenceGraceMs
  }

  currentLease(): VisitLease | null {
    return this.active?.lease ?? null
  }

  currentVisitId(): string | null {
    return this.active?.lease.visitId ?? null
  }

  isCurrent(lease: VisitLease): boolean {
    return !this.disposed && visitLeaseRegistry.isCurrent(lease)
  }

  observeVisible(candidate: VisitCandidate<TDetection>): VisitLease {
    if (this.disposed) throw new Error('VisitOrchestrator is disposed.')
    this.clearAbsenceTimer()
    this.farewellAbort?.abort()
    this.farewellAbort = null

    const current = this.active
    if (current?.lease.visitId === candidate.visitId && this.isCurrent(current.lease)) {
      current.candidate = candidate
      return current.lease
    }

    const previous = current
    const activation = visitLeaseRegistry.activate(candidate.visitId)
    this.active = {
      lease: activation.lease,
      candidate,
      greetingStarted: false,
      greeted: false,
    }

    if (previous) {
      const ended: VisitEndContext<TDetection> = {
        lease: previous.lease,
        candidate: previous.candidate,
        reason: 'visit_replaced',
      }
      this.runBackground(this.hooks.onPreempt(ended, candidate))
      this.runBackground(this.hooks.onArchive(ended))
    }

    console.info('[ProximityDebug] visit-orchestrator-activated', {
      visitId: activation.lease.visitId,
      epoch: activation.lease.epoch,
      replacedVisitId: previous?.lease.visitId ?? null,
    })
    return activation.lease
  }

  requestGreeting(candidate: VisitCandidate<TDetection>): void {
    const lease = this.observeVisible(candidate)
    const current = this.active
    if (!current || current.greetingStarted || current.greeted) return
    current.greetingStarted = true

    const task = (async () => {
      let greetingCompleted = false
      try {
        greetingCompleted = await this.hooks.onGreeting(lease, candidate)
      } catch (error) {
        console.error('[ProximityDebug] visit-greeting-error', {
          visitId: lease.visitId,
          epoch: lease.epoch,
          message: error instanceof Error ? error.message : String(error),
        })
      }
      if (!this.isCurrent(lease) || this.active?.lease.epoch !== lease.epoch) return

      // A greeting is presentation, not a gate for command reception. Visitor
      // barge-in commonly interrupts the welcome audio; that expected interruption
      // must hand control to the command loop instead of leaving an open microphone
      // with no consumer. Mark the greeting attempt complete to prevent duplicate
      // greetings and always start conversation while the Visit remains current.
      this.active.greetingStarted = false
      this.active.greeted = true
      if (!greetingCompleted) {
        console.info('[ProximityDebug] conversation-started-without-completed-greeting', {
          visitId: lease.visitId,
          epoch: lease.epoch,
          reason: 'greeting_interrupted_or_unavailable',
        })
      }
      await this.hooks.onConversation(lease, this.active.candidate)
    })()
    this.runBackground(task)
  }

  markAbsent(reason: string): void {
    if (this.disposed || !this.active || this.absenceTimer !== null) return
    const visitId = this.active.lease.visitId
    const epoch = this.active.lease.epoch
    console.info('[ProximityDebug] visit-absence-grace-started', {
      visitId,
      epoch,
      reason,
      graceMs: this.absenceGraceMs,
    })
    this.absenceTimer = window.setTimeout(() => {
      this.absenceTimer = null
      const current = this.active
      if (!current || current.lease.visitId !== visitId || current.lease.epoch !== epoch) return
      this.finishCurrent(`${reason}_confirmed`)
    }, this.absenceGraceMs)
  }

  finishFromRemote(visitId: string, reason: string): void {
    if (this.active?.lease.visitId !== visitId) {
      console.info('[ProximityDebug] stale-visit-end-ignored', {
        endedVisitId: visitId,
        currentVisitId: this.active?.lease.visitId ?? null,
        reason,
      })
      return
    }
    this.finishCurrent(reason)
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.clearAbsenceTimer()
    this.farewellAbort?.abort()
    this.farewellAbort = null
    const current = this.active
    this.active = null
    if (current) {
      visitLeaseRegistry.revoke(current.lease.visitId, 'orchestrator_disposed')
      const ended: VisitEndContext<TDetection> = {
        lease: current.lease,
        candidate: current.candidate,
        reason: 'orchestrator_disposed',
      }
      this.runBackground(this.hooks.onPreempt(ended, null))
      this.runBackground(this.hooks.onArchive(ended))
    }
  }

  private finishCurrent(reason: string): void {
    const current = this.active
    if (!current) return
    this.clearAbsenceTimer()
    this.active = null
    visitLeaseRegistry.revoke(current.lease.visitId, reason)
    const ended: VisitEndContext<TDetection> = {
      lease: current.lease,
      candidate: current.candidate,
      reason,
    }

    // Real-time resources are revoked immediately. These callbacks are never a
    // barrier for the next visitor.
    this.runBackground(this.hooks.onPreempt(ended, null))
    this.runBackground(this.hooks.onArchive(ended))

    const farewellAbort = new AbortController()
    this.farewellAbort?.abort()
    this.farewellAbort = farewellAbort
    const farewellTask = (async () => {
      await new Promise<void>((resolve) => window.setTimeout(resolve, 120))
      if (farewellAbort.signal.aborted || this.active || this.disposed) return
      try {
        await this.hooks.onFarewell(ended, farewellAbort.signal)
      } finally {
        if (this.farewellAbort === farewellAbort) this.farewellAbort = null
      }
    })()
    this.runBackground(farewellTask)

    console.info('[ProximityDebug] visit-orchestrator-revoked', {
      visitId: ended.lease.visitId,
      epoch: ended.lease.epoch,
      reason,
      nextVisitCanStartImmediately: true,
    })
  }

  private clearAbsenceTimer(): void {
    if (this.absenceTimer === null) return
    window.clearTimeout(this.absenceTimer)
    this.absenceTimer = null
  }

  private runBackground(value: void | Promise<unknown>): void {
    if (!value || typeof (value as Promise<unknown>).then !== 'function') return
    const promise = Promise.resolve(value)
    this.background.add(promise)
    void promise.finally(() => this.background.delete(promise))
  }
}
