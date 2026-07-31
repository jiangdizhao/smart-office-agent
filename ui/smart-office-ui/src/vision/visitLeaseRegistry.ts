export type VisitLease = {
  visitId: string
  epoch: number
  startedAt: number
  signal: AbortSignal
}

type ActiveVisitLease = VisitLease & {
  abortController: AbortController
}

export type VisitActivation = {
  lease: VisitLease
  replaced: VisitLease | null
}

class VisitLeaseRegistry {
  private epoch = 0
  private active: ActiveVisitLease | null = null

  activate(visitId: string): VisitActivation {
    const clean = visitId.trim()
    if (!clean) throw new Error('visitId is required')
    if (this.active?.visitId === clean && !this.active.signal.aborted) {
      return { lease: this.publicLease(this.active), replaced: null }
    }

    const replaced = this.active ? this.publicLease(this.active) : null
    this.active?.abortController.abort()
    this.epoch += 1
    const abortController = new AbortController()
    this.active = {
      visitId: clean,
      epoch: this.epoch,
      startedAt: performance.now(),
      signal: abortController.signal,
      abortController,
    }
    const lease = this.publicLease(this.active)
    window.dispatchEvent(
      new CustomEvent('smartoffice:visit-activated', {
        detail: {
          visitId: lease.visitId,
          epoch: lease.epoch,
          replacedVisitId: replaced?.visitId ?? null,
          replacedEpoch: replaced?.epoch ?? null,
        },
      }),
    )
    return { lease, replaced }
  }

  revoke(visitId: string, reason: string): VisitLease | null {
    const current = this.active
    if (!current || current.visitId !== visitId) return null
    const revoked = this.publicLease(current)
    current.abortController.abort()
    this.active = null
    this.epoch += 1
    window.dispatchEvent(
      new CustomEvent('smartoffice:visit-revoked', {
        detail: {
          visitId: revoked.visitId,
          epoch: revoked.epoch,
          reason,
          fenceEpoch: this.epoch,
        },
      }),
    )
    return revoked
  }

  current(): VisitLease | null {
    return this.active ? this.publicLease(this.active) : null
  }

  isCurrent(lease: Pick<VisitLease, 'visitId' | 'epoch'> | null | undefined): boolean {
    return Boolean(
      lease &&
        this.active &&
        !this.active.signal.aborted &&
        this.active.visitId === lease.visitId &&
        this.active.epoch === lease.epoch,
    )
  }

  currentEpoch(): number {
    return this.epoch
  }

  reset(reason = 'registry_reset'): void {
    const current = this.active
    if (current) this.revoke(current.visitId, reason)
    else this.epoch += 1
  }

  private publicLease(active: ActiveVisitLease): VisitLease {
    return {
      visitId: active.visitId,
      epoch: active.epoch,
      startedAt: active.startedAt,
      signal: active.signal,
    }
  }
}

export const visitLeaseRegistry = new VisitLeaseRegistry()
