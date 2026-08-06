import type { RemoteVisionDetection, RemoteVisitEnded } from './remoteVisionClient'

export class RemoteVisitReplacementTracker {
  private lastVisible: RemoteVisionDetection | null = null

  reset(): void {
    this.lastVisible = null
  }

  observe(detection: RemoteVisionDetection): RemoteVisitEnded | null {
    const previous = this.lastVisible
    this.lastVisible = detection
    if (!previous || previous.visitor_session_id === detection.visitor_session_id) {
      return null
    }
    return {
      visitor_session_id: previous.visitor_session_id,
      visit_id: previous.visitor_session_id,
      last_track_id: previous.track_id,
      identity_id: previous.identity_id ?? null,
      display_name: previous.display_name ?? null,
      sequence: 0,
      source_event: 'client_state_visit_replaced',
    }
  }

  enrichEnded(visit: RemoteVisitEnded): RemoteVisitEnded {
    const cached = this.lastVisible?.visitor_session_id === visit.visit_id
      ? this.lastVisible
      : null
    if (cached) this.lastVisible = null
    return {
      ...visit,
      last_track_id: visit.last_track_id ?? cached?.track_id ?? null,
      identity_id: visit.identity_id ?? cached?.identity_id ?? null,
      display_name: visit.display_name ?? cached?.display_name ?? null,
    }
  }
}
