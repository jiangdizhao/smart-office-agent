export type ProactiveListeningGateReason =
  | 'eligible'
  | 'stabilizing'
  | 'face_missing'
  | 'face_missing_grace'
  | 'visitor_too_far'
  | 'visitor_too_far_grace'
  | 'primary_absent'
  | 'primary_absent_grace'

export type ProactiveListeningObservation = {
  primaryPresent: boolean
  faceVisible: boolean
  bodyAreaRatio: number
}

export type ProactiveListeningGateConfig = {
  enterBodyRatio: number
  exitBodyRatio: number
  enterStableMs: number
  exitGraceMs: number
}

export type ProactiveListeningGateSnapshot = {
  eligible: boolean
  reason: ProactiveListeningGateReason
  primaryPresent: boolean
  faceVisible: boolean
  bodyAreaRatio: number
  enterBodyRatio: number
  exitBodyRatio: number
  transitionAgeMs: number
}

const DEFAULT_CONFIG: ProactiveListeningGateConfig = {
  enterBodyRatio: 0.11,
  exitBodyRatio: 0.075,
  enterStableMs: 600,
  exitGraceMs: 1_200,
}

function finiteNonNegative(value: number, fallback: number): number {
  return Number.isFinite(value) && value >= 0 ? value : fallback
}

function blockedReason(
  observation: ProactiveListeningObservation,
): 'face_missing' | 'visitor_too_far' | 'primary_absent' {
  if (!observation.primaryPresent) return 'primary_absent'
  if (!observation.faceVisible) return 'face_missing'
  return 'visitor_too_far'
}

function graceReason(
  reason: 'face_missing' | 'visitor_too_far' | 'primary_absent',
): ProactiveListeningGateReason {
  if (reason === 'face_missing') return 'face_missing_grace'
  if (reason === 'visitor_too_far') return 'visitor_too_far_grace'
  return 'primary_absent_grace'
}

export class ProactiveListeningGate {
  private readonly config: ProactiveListeningGateConfig
  private active = false
  private enterSinceMs: number | null = null
  private blockedSinceMs: number | null = null

  constructor(config: Partial<ProactiveListeningGateConfig> = {}) {
    const enterBodyRatio = finiteNonNegative(
      config.enterBodyRatio ?? DEFAULT_CONFIG.enterBodyRatio,
      DEFAULT_CONFIG.enterBodyRatio,
    )
    const requestedExitRatio = finiteNonNegative(
      config.exitBodyRatio ?? DEFAULT_CONFIG.exitBodyRatio,
      DEFAULT_CONFIG.exitBodyRatio,
    )
    this.config = {
      enterBodyRatio,
      exitBodyRatio: Math.min(enterBodyRatio, requestedExitRatio),
      enterStableMs: finiteNonNegative(
        config.enterStableMs ?? DEFAULT_CONFIG.enterStableMs,
        DEFAULT_CONFIG.enterStableMs,
      ),
      exitGraceMs: finiteNonNegative(
        config.exitGraceMs ?? DEFAULT_CONFIG.exitGraceMs,
        DEFAULT_CONFIG.exitGraceMs,
      ),
    }
  }

  reset(): void {
    this.active = false
    this.enterSinceMs = null
    this.blockedSinceMs = null
  }

  update(
    observation: ProactiveListeningObservation,
    nowMs = performance.now(),
  ): ProactiveListeningGateSnapshot {
    const normalized: ProactiveListeningObservation = {
      primaryPresent: Boolean(observation.primaryPresent),
      faceVisible: Boolean(observation.faceVisible),
      bodyAreaRatio: Math.max(0, Number(observation.bodyAreaRatio) || 0),
    }

    if (this.active) {
      const remainsEligible =
        normalized.primaryPresent &&
        normalized.faceVisible &&
        normalized.bodyAreaRatio >= this.config.exitBodyRatio

      if (remainsEligible) {
        this.blockedSinceMs = null
        this.enterSinceMs = null
        return this.snapshot(normalized, 'eligible', 0)
      }

      const reason = blockedReason(normalized)
      this.blockedSinceMs ??= nowMs
      const blockedForMs = Math.max(0, nowMs - this.blockedSinceMs)
      if (blockedForMs < this.config.exitGraceMs) {
        return this.snapshot(normalized, graceReason(reason), blockedForMs)
      }

      this.active = false
      this.enterSinceMs = null
      this.blockedSinceMs = null
      return this.snapshot(normalized, reason, blockedForMs)
    }

    const canEnter =
      normalized.primaryPresent &&
      normalized.faceVisible &&
      normalized.bodyAreaRatio >= this.config.enterBodyRatio

    if (!canEnter) {
      this.enterSinceMs = null
      this.blockedSinceMs = null
      return this.snapshot(normalized, blockedReason(normalized), 0)
    }

    this.enterSinceMs ??= nowMs
    const stableForMs = Math.max(0, nowMs - this.enterSinceMs)
    if (stableForMs < this.config.enterStableMs) {
      return this.snapshot(normalized, 'stabilizing', stableForMs)
    }

    this.active = true
    this.enterSinceMs = null
    this.blockedSinceMs = null
    return this.snapshot(normalized, 'eligible', stableForMs)
  }

  private snapshot(
    observation: ProactiveListeningObservation,
    reason: ProactiveListeningGateReason,
    transitionAgeMs: number,
  ): ProactiveListeningGateSnapshot {
    return {
      eligible: this.active,
      reason,
      primaryPresent: observation.primaryPresent,
      faceVisible: observation.faceVisible,
      bodyAreaRatio: observation.bodyAreaRatio,
      enterBodyRatio: this.config.enterBodyRatio,
      exitBodyRatio: this.config.exitBodyRatio,
      transitionAgeMs,
    }
  }
}
