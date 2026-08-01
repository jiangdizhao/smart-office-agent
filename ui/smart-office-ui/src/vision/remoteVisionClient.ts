import type { ProximityDetection } from './proximityFaceMonitor'
import { RemoteVisitReplacementTracker } from './remoteVisionVisitReplacement'

export type RemoteVisionStatus =
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'offline'
  | 'stopped'

export type VisitorGreetingKind =
  | 'new_anonymous'
  | 'returning_anonymous'
  | 'registered_identity'

export type RemoteVisionDetection = ProximityDetection & {
  track_id: number
  visitor_session_id: string
  visit_id?: string | null
  visit_state?: string | null
  provisional_session_id?: string | null
  session_stable: boolean
  session_age_seconds: number
  session_last_seen_age_seconds?: number
  session_recovery_count: number
  returning_visitor: boolean
  greeting_kind: VisitorGreetingKind
  track_state?: string | null
  visible: boolean
  primary: boolean
  engaged: boolean
  greeting_eligible: boolean
  client_entry_area_eligible: boolean
  client_hold_area_eligible: boolean
  client_entry_min_body_area_ratio: number
  client_hold_min_body_area_ratio: number
  identity_id?: string | null
  display_name?: string | null
  identity_similarity?: number | null
  face_quality_score: number
  recognition_usable: boolean
  enrollment_usable: boolean
  schema_version?: string | null
  server_instance_id?: string | null
  snapshot_revision?: number
  frame_age_ms?: number | null
  service_ready?: boolean
  scene_state?: string | null
  person_count?: number
  visitor_count?: number
  source_event: string
  updated_at: string
}

export type RemoteVisitEnded = {
  visitor_session_id: string
  visit_id: string
  last_track_id?: number | null
  identity_id?: string | null
  display_name?: string | null
  sequence: number
  server_instance_id?: string | null
  source_event: string
}

export function isRemoteVisionDetection(
  detection: ProximityDetection | RemoteVisionDetection | null,
): detection is RemoteVisionDetection {
  return Boolean(detection && 'visitor_session_id' in detection && 'track_id' in detection)
}

type RemoteFace = {
  detected?: boolean
  area_ratio?: number
  confidence?: number
  frontal_score?: number
  quality_score?: number
  recognition_usable?: boolean
  enrollment_usable?: boolean
  stable_frames?: number
}

type RemoteIdentity = {
  identity_id?: string | null
  display_name?: string | null
  similarity?: number
}

type RemoteVisitor = {
  track_id?: number
  visitor_session_id?: string | null
  visit_id?: string | null
  visit_state?: string | null
  provisional_session_id?: string | null
  session_stable?: boolean
  session_age_seconds?: number
  session_last_seen_age_seconds?: number
  session_recovery_count?: number
  returning_visitor?: boolean
  greeting_kind?: VisitorGreetingKind
  state?: string | null
  visible?: boolean
  primary?: boolean
  engaged?: boolean
  score?: number
  body_area_ratio?: number
  center_x?: number
  center_y?: number
  face?: RemoteFace | null
  identity?: RemoteIdentity | null
  greeting_eligible?: boolean
}

type ClientState = {
  schema_version?: string
  server_instance_id?: string
  snapshot_revision?: number
  frame_age_ms?: number | null
  vision_stale?: boolean
  ready?: boolean
  scene_state?: string
  person_count?: number
  primary?: RemoteVisitor | null
  retained_primary?: RemoteVisitor | null
  visitors?: RemoteVisitor[]
}

type EventEnvelope = {
  protocol_version?: string
  server_instance_id?: string
  event_id?: string
  sequence?: number
  type?: string
  payload?: Record<string, unknown>
}

type RemoteVisionClientOptions = {
  url?: string
  onStatus: (status: RemoteVisionStatus, detail: string) => void
  onDetection: (detection: RemoteVisionDetection | null) => void
  onGreetingCandidate: (detection: RemoteVisionDetection) => void
  onSessionExpired?: (visitorSessionId: string) => void
  onVisitEnded?: (visit: RemoteVisitEnded) => void
}

const STATE_POLL_MS = 750
const PING_MS = 10_000
const FRESHNESS_CHECK_MS = 250
const REMOTE_MESSAGE_STALE_MS = 2_500
const FRAME_STALE_MS = 2_500
const MAX_RECONNECT_MS = 10_000
const EVENT_CACHE_LIMIT = 512
const DEFAULT_ENTRY_MIN_BODY_AREA_RATIO = 0.1
const DEFAULT_HOLD_MIN_BODY_AREA_RATIO = 0.045

function clamp(value: unknown): number {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.max(0, Math.min(1, numeric))
}

function configuredRatio(name: string, fallback: number): number {
  const configured = Number(import.meta.env[name])
  if (!Number.isFinite(configured)) return fallback
  return Math.max(0, Math.min(1, configured))
}

const REMOTE_ENTRY_MIN_BODY_AREA_RATIO = configuredRatio(
  'VITE_REMOTE_VISION_ENTRY_MIN_BODY_AREA_RATIO',
  DEFAULT_ENTRY_MIN_BODY_AREA_RATIO,
)
const REMOTE_HOLD_MIN_BODY_AREA_RATIO = Math.min(
  configuredRatio(
    'VITE_REMOTE_VISION_HOLD_MIN_BODY_AREA_RATIO',
    DEFAULT_HOLD_MIN_BODY_AREA_RATIO,
  ),
  REMOTE_ENTRY_MIN_BODY_AREA_RATIO,
)

function optionalNumber(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function greetingKind(value: unknown): VisitorGreetingKind {
  if (value === 'registered_identity') return 'registered_identity'
  if (value === 'returning_anonymous') return 'returning_anonymous'
  return 'new_anonymous'
}

export function remoteVisionUrl(): string {
  const configured = String(import.meta.env.VITE_VISION_SERVER_WS ?? '').trim()
  if (configured) return configured
  const httpBase = String(import.meta.env.VITE_VISION_SERVER_HTTP ?? '').trim().replace(/\/$/, '')
  if (httpBase) {
    return `${httpBase.replace(/^http:/i, 'ws:').replace(/^https:/i, 'wss:')}/ws/v1/events`
  }
  return 'ws://127.0.0.1:8015/ws/v1/events'
}

function toDetection(
  visitor: RemoteVisitor,
  sourceEvent: string,
  state: ClientState,
): RemoteVisionDetection | null {
  const sessionId = String(visitor.visitor_session_id ?? visitor.visit_id ?? '').trim()
  const trackId = Number(visitor.track_id)
  if (!Number.isFinite(trackId) || trackId <= 0 || !sessionId) return null
  const face = visitor.face ?? {}
  const bodyConfidence = clamp(visitor.score)
  const faceConfidence = clamp(face.confidence)
  const bodyAreaRatio = clamp(visitor.body_area_ratio)
  const visitors = Array.isArray(state.visitors) ? state.visitors : []
  return {
    body_area_ratio: bodyAreaRatio,
    body_confidence: bodyConfidence,
    face_area_ratio: clamp(face.area_ratio),
    face_confidence: faceConfidence,
    face_inside_body: Boolean(face.detected),
    confidence: Math.max(bodyConfidence, faceConfidence),
    frontal_score: clamp(face.frontal_score),
    center_x: clamp(visitor.center_x),
    center_y: clamp(visitor.center_y),
    stable_frames: Math.max(1, Number(face.stable_frames) || 1),
    detector: 'rtx-vision-phase6.1',
    track_id: trackId,
    visitor_session_id: sessionId,
    visit_id: visitor.visit_id ?? sessionId,
    visit_state: visitor.visit_state ?? null,
    provisional_session_id: visitor.provisional_session_id ?? null,
    session_stable: Boolean(visitor.session_stable),
    session_age_seconds: Math.max(0, Number(visitor.session_age_seconds) || 0),
    session_last_seen_age_seconds: Math.max(
      0,
      Number(visitor.session_last_seen_age_seconds) || 0,
    ),
    session_recovery_count: Math.max(0, Number(visitor.session_recovery_count) || 0),
    returning_visitor: Boolean(visitor.returning_visitor),
    greeting_kind: greetingKind(visitor.greeting_kind),
    track_state: visitor.state ?? null,
    visible: Boolean(visitor.visible),
    primary: Boolean(visitor.primary),
    engaged: Boolean(visitor.engaged),
    greeting_eligible: Boolean(visitor.greeting_eligible),
    client_entry_area_eligible: bodyAreaRatio >= REMOTE_ENTRY_MIN_BODY_AREA_RATIO,
    client_hold_area_eligible: bodyAreaRatio >= REMOTE_HOLD_MIN_BODY_AREA_RATIO,
    client_entry_min_body_area_ratio: REMOTE_ENTRY_MIN_BODY_AREA_RATIO,
    client_hold_min_body_area_ratio: REMOTE_HOLD_MIN_BODY_AREA_RATIO,
    identity_id: visitor.identity?.identity_id ?? null,
    display_name: visitor.identity?.display_name ?? null,
    identity_similarity: optionalNumber(visitor.identity?.similarity),
    face_quality_score: clamp(face.quality_score),
    recognition_usable: Boolean(face.recognition_usable),
    enrollment_usable: Boolean(face.enrollment_usable),
    schema_version: state.schema_version ?? null,
    server_instance_id: state.server_instance_id ?? null,
    snapshot_revision: Math.max(0, Number(state.snapshot_revision) || 0),
    frame_age_ms: optionalNumber(state.frame_age_ms),
    service_ready: Boolean(state.ready),
    scene_state: state.scene_state ?? null,
    person_count: Math.max(0, Number(state.person_count) || 0),
    visitor_count: visitors.length,
    source_event: sourceEvent,
    updated_at: new Date().toISOString(),
  }
}

export class RemoteVisionClient {
  private readonly url: string
  private readonly options: RemoteVisionClientOptions
  private readonly replacementTracker = new RemoteVisitReplacementTracker()
  private socket: WebSocket | null = null
  private stopped = true
  private reconnectAttempt = 0
  private reconnectTimer: number | null = null
  private statePollTimer: number | null = null
  private pingTimer: number | null = null
  private freshnessTimer: number | null = null
  private seenEventIds = new Set<string>()
  private seenEventOrder: string[] = []
  private serverInstanceId: string | null = null
  private lastSnapshotRevision = 0
  private lastMessageAt = 0
  private stalePublished = false

  constructor(options: RemoteVisionClientOptions) {
    this.options = options
    this.url = options.url?.trim() || remoteVisionUrl()
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.reconnectAttempt = 0
    this.connect('connecting')
  }

  stop(): void {
    this.stopped = true
    this.clearTimers()
    this.resetServerEpoch(null)
    const socket = this.socket
    this.socket = null
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, 'client stopped')
    this.options.onDetection(null)
    this.options.onStatus('stopped', this.url)
  }

  requestState(): void {
    this.send({ type: 'get_client_state' })
  }

  private connect(status: 'connecting' | 'reconnecting'): void {
    if (this.stopped) return
    this.options.onStatus(status, this.url)
    let socket: WebSocket
    try {
      socket = new WebSocket(this.url)
    } catch (error) {
      this.publishStale(`WebSocket construction failed: ${String(error)}`)
      this.scheduleReconnect(String(error))
      return
    }
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket || this.stopped) return
      this.reconnectAttempt = 0
      this.lastMessageAt = performance.now()
      this.stalePublished = false
      this.options.onStatus('connected', this.url)
      this.send({ type: 'get_client_state' })
      this.startTimers()
    }
    socket.onmessage = (event) => {
      if (this.socket !== socket || this.stopped) return
      this.lastMessageAt = performance.now()
      this.stalePublished = false
      this.handleMessage(event.data)
    }
    socket.onerror = () => {
      if (this.socket === socket && !this.stopped) {
        this.publishStale(`WebSocket error: ${this.url}`)
      }
    }
    socket.onclose = (event) => {
      if (this.socket === socket) this.socket = null
      this.clearLiveTimers()
      this.publishStale(`closed ${event.code}${event.reason ? `: ${event.reason}` : ''}`)
      if (!this.stopped) {
        this.scheduleReconnect(`closed ${event.code}${event.reason ? `: ${event.reason}` : ''}`)
      }
    }
  }

  private startTimers(): void {
    this.clearLiveTimers()
    this.statePollTimer = window.setInterval(() => this.requestState(), STATE_POLL_MS)
    this.pingTimer = window.setInterval(
      () => this.send({ type: 'ping', client_time: new Date().toISOString() }),
      PING_MS,
    )
    this.freshnessTimer = window.setInterval(() => {
      if (!this.lastMessageAt) return
      if (performance.now() - this.lastMessageAt > REMOTE_MESSAGE_STALE_MS) {
        this.publishStale('RTX vision messages are stale.')
      }
    }, FRESHNESS_CHECK_MS)
  }

  private scheduleReconnect(detail: string): void {
    if (this.stopped || this.reconnectTimer !== null) return
    this.reconnectAttempt += 1
    const delay = Math.min(1_000 * 2 ** Math.min(this.reconnectAttempt - 1, 4), MAX_RECONNECT_MS)
    this.options.onStatus('reconnecting', `${detail}; retry in ${(delay / 1_000).toFixed(0)}s`)
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect('reconnecting')
    }, delay)
  }

  private publishStale(detail: string): void {
    this.options.onDetection(null)
    if (this.stalePublished || this.stopped) return
    this.stalePublished = true
    this.options.onStatus('offline', detail)
  }

  private clearLiveTimers(): void {
    if (this.statePollTimer !== null) window.clearInterval(this.statePollTimer)
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer)
    if (this.freshnessTimer !== null) window.clearInterval(this.freshnessTimer)
    this.statePollTimer = null
    this.pingTimer = null
    this.freshnessTimer = null
  }

  private clearTimers(): void {
    this.clearLiveTimers()
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
  }

  private send(message: Record<string, unknown>): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return
    this.socket.send(JSON.stringify(message))
  }

  private resetServerEpoch(next: string | null): void {
    this.serverInstanceId = next
    this.lastSnapshotRevision = 0
    this.seenEventIds.clear()
    this.seenEventOrder = []
    this.replacementTracker.reset()
  }

  private observeServerInstance(value: unknown): void {
    const next = String(value ?? '').trim()
    if (!next || next === this.serverInstanceId) return
    const previous = this.serverInstanceId
    this.resetServerEpoch(next)
    this.options.onDetection(null)
    console.info('[ProximityDebug] remote-vision-server-instance-changed', {
      previousServerInstanceId: previous,
      serverInstanceId: next,
    })
  }

  private handleMessage(raw: unknown): void {
    let envelope: EventEnvelope
    try {
      envelope = JSON.parse(String(raw)) as EventEnvelope
    } catch {
      return
    }
    const payload = envelope.payload ?? {}
    this.observeServerInstance(envelope.server_instance_id ?? payload.server_instance_id)

    const eventId = String(envelope.event_id ?? '')
    if (eventId) {
      if (this.seenEventIds.has(eventId)) return
      this.seenEventIds.add(eventId)
      this.seenEventOrder.push(eventId)
      while (this.seenEventOrder.length > EVENT_CACHE_LIMIT) {
        const removed = this.seenEventOrder.shift()
        if (removed) this.seenEventIds.delete(removed)
      }
    }

    const eventType = String(envelope.type ?? '')
    if (eventType === 'server_ready' || eventType === 'heartbeat' || eventType === 'pong') {
      return
    }
    if (eventType === 'vision_stale') {
      this.publishStale('RTX camera or vision pipeline is stale.')
      return
    }
    if (eventType === 'vision_recovered') {
      this.requestState()
      return
    }
    if (eventType === 'client_state_snapshot') {
      this.handleClientState(payload as ClientState)
      return
    }
    if (
      eventType === 'visitor_engaged' ||
      eventType === 'visitor_identified' ||
      eventType === 'visitor_session_started' ||
      eventType === 'visitor_session_recovered' ||
      eventType === 'primary_visitor_changed'
    ) {
      this.requestState()
      return
    }
    if (eventType === 'visit_ended' || eventType === 'visitor_session_expired') {
      const sessionId = String(payload.visit_id ?? payload.visitor_session_id ?? '').trim()
      if (!sessionId) return
      const visit = this.replacementTracker.enrichEnded({
        visitor_session_id: sessionId,
        visit_id: sessionId,
        last_track_id: optionalNumber(payload.last_track_id),
        identity_id: String(payload.identity_id ?? '').trim() || null,
        display_name: String(payload.display_name ?? '').trim() || null,
        sequence: Math.max(0, Number(envelope.sequence) || 0),
        server_instance_id: this.serverInstanceId,
        source_event: eventType,
      })
      this.options.onVisitEnded?.(visit)
      this.options.onSessionExpired?.(sessionId)
    }
  }

  private handleClientState(state: ClientState): void {
    this.observeServerInstance(state.server_instance_id)
    const revision = Math.max(0, Number(state.snapshot_revision) || 0)
    if (revision > 0 && revision <= this.lastSnapshotRevision) return
    if (revision > 0) this.lastSnapshotRevision = revision

    const frameAge = optionalNumber(state.frame_age_ms)
    if (state.vision_stale || (frameAge !== null && frameAge > FRAME_STALE_MS)) {
      this.publishStale(`RTX frame is stale (${frameAge ?? 'unknown'} ms).`)
      return
    }

    const primary = state.primary
    if (!primary || !primary.visible) {
      this.options.onDetection(null)
      return
    }
    const detection = toDetection(primary, 'client_state_snapshot', state)
    if (!detection) {
      this.options.onDetection(null)
      return
    }
    if (!detection.client_hold_area_eligible) {
      console.info('[ProximityDebug] remote-primary-below-hold-area', {
        visitId: detection.visit_id ?? detection.visitor_session_id,
        bodyAreaRatio: detection.body_area_ratio,
        holdMinimum: detection.client_hold_min_body_area_ratio,
        entryMinimum: detection.client_entry_min_body_area_ratio,
      })
      this.options.onDetection(null)
      return
    }
    const replacedVisit = this.replacementTracker.observe(detection)
    if (replacedVisit) {
      replacedVisit.server_instance_id = this.serverInstanceId
      this.options.onVisitEnded?.(replacedVisit)
      this.options.onSessionExpired?.(replacedVisit.visit_id)
    }
    this.options.onDetection(detection)
    if (
      primary.greeting_eligible &&
      detection.client_entry_area_eligible &&
      detection.session_stable &&
      Boolean(detection.visitor_session_id)
    ) {
      this.options.onGreetingCandidate(detection)
    }
  }
}
