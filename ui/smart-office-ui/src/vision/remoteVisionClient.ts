import type { ProximityDetection } from './proximityFaceMonitor'

export type RemoteVisionStatus =
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'offline'
  | 'stopped'

export type RemoteVisionDetection = ProximityDetection & {
  track_id: number
  visitor_session_id: string
  track_state?: string | null
  visible: boolean
  primary: boolean
  engaged: boolean
  greeting_eligible: boolean
  identity_id?: string | null
  display_name?: string | null
  identity_similarity?: number | null
  face_quality_score: number
  recognition_usable: boolean
  enrollment_usable: boolean
  schema_version?: string | null
  service_ready?: boolean
  scene_state?: string | null
  person_count?: number
  visitor_count?: number
  source_event: string
  updated_at: string
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
  ready?: boolean
  scene_state?: string
  person_count?: number
  primary?: RemoteVisitor | null
  visitors?: RemoteVisitor[]
}

type EventEnvelope = {
  protocol_version?: string
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
}

const STATE_POLL_MS = 750
const PING_MS = 10_000
const MAX_RECONNECT_MS = 10_000
const EVENT_CACHE_LIMIT = 512

function clamp(value: unknown): number {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.max(0, Math.min(1, numeric))
}

function optionalNumber(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
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
  const sessionId = String(visitor.visitor_session_id ?? '').trim()
  const trackId = Number(visitor.track_id)
  if (!sessionId || !Number.isFinite(trackId) || trackId <= 0) return null
  const face = visitor.face ?? {}
  const bodyConfidence = clamp(visitor.score)
  const faceConfidence = clamp(face.confidence)
  const visitors = Array.isArray(state.visitors) ? state.visitors : []
  return {
    body_area_ratio: clamp(visitor.body_area_ratio),
    body_confidence: bodyConfidence,
    face_area_ratio: clamp(face.area_ratio),
    face_confidence: faceConfidence,
    face_inside_body: Boolean(face.detected),
    confidence: Math.max(bodyConfidence, faceConfidence),
    frontal_score: clamp(face.frontal_score),
    center_x: clamp(visitor.center_x),
    center_y: clamp(visitor.center_y),
    stable_frames: Math.max(1, Number(face.stable_frames) || 1),
    detector: 'rtx-vision-phase5',
    track_id: trackId,
    visitor_session_id: sessionId,
    track_state: visitor.state ?? null,
    visible: Boolean(visitor.visible),
    primary: Boolean(visitor.primary),
    engaged: Boolean(visitor.engaged),
    greeting_eligible: Boolean(visitor.greeting_eligible),
    identity_id: visitor.identity?.identity_id ?? null,
    display_name: visitor.identity?.display_name ?? null,
    identity_similarity: optionalNumber(visitor.identity?.similarity),
    face_quality_score: clamp(face.quality_score),
    recognition_usable: Boolean(face.recognition_usable),
    enrollment_usable: Boolean(face.enrollment_usable),
    schema_version: state.schema_version ?? null,
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
  private socket: WebSocket | null = null
  private stopped = true
  private reconnectAttempt = 0
  private reconnectTimer: number | null = null
  private statePollTimer: number | null = null
  private pingTimer: number | null = null
  private seenEventIds = new Set<string>()
  private seenEventOrder: string[] = []

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
      this.scheduleReconnect(String(error))
      return
    }
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket || this.stopped) return
      this.reconnectAttempt = 0
      this.options.onStatus('connected', this.url)
      this.send({ type: 'get_client_state' })
      this.startTimers()
    }
    socket.onmessage = (event) => {
      if (this.socket !== socket || this.stopped) return
      this.handleMessage(event.data)
    }
    socket.onerror = () => {
      if (this.socket === socket && !this.stopped) {
        this.options.onStatus('offline', `WebSocket error: ${this.url}`)
      }
    }
    socket.onclose = (event) => {
      if (this.socket === socket) this.socket = null
      this.clearLiveTimers()
      if (!this.stopped) this.scheduleReconnect(`closed ${event.code}${event.reason ? `: ${event.reason}` : ''}`)
    }
  }

  private startTimers(): void {
    this.clearLiveTimers()
    this.statePollTimer = window.setInterval(() => this.requestState(), STATE_POLL_MS)
    this.pingTimer = window.setInterval(
      () => this.send({ type: 'ping', client_time: new Date().toISOString() }),
      PING_MS,
    )
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

  private clearLiveTimers(): void {
    if (this.statePollTimer !== null) window.clearInterval(this.statePollTimer)
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer)
    this.statePollTimer = null
    this.pingTimer = null
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

  private handleMessage(raw: unknown): void {
    let envelope: EventEnvelope
    try {
      envelope = JSON.parse(String(raw)) as EventEnvelope
    } catch {
      return
    }
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
    const payload = envelope.payload ?? {}
    if (eventType === 'client_state_snapshot') {
      this.handleClientState(payload as ClientState)
      return
    }
    if (
      eventType === 'visitor_engaged' ||
      eventType === 'visitor_identified' ||
      eventType === 'visitor_session_recovered' ||
      eventType === 'primary_visitor_changed'
    ) {
      this.requestState()
      return
    }
    if (eventType === 'visitor_session_expired') {
      const sessionId = String(payload.visitor_session_id ?? '')
      if (sessionId) this.options.onSessionExpired?.(sessionId)
    }
  }

  private handleClientState(state: ClientState): void {
    const primary = state.primary
    if (!primary) {
      this.options.onDetection(null)
      return
    }
    const detection = toDetection(primary, 'client_state_snapshot', state)
    this.options.onDetection(detection)
    if (detection && primary.greeting_eligible) {
      this.options.onGreetingCandidate(detection)
    }
  }
}
