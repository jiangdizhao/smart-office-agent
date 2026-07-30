# Vision Event Protocol v2: anonymous visitor tracks

Transport remains:

```text
ws://<vision-server>:8015/ws/v1/events
```

The envelope remains protocol version `1.0` for backward-compatible transport. Phase 2 extends
the event types and payloads; it does not claim persistent human identity.

## Track lifecycle events

### `visitor_entered`

Emitted after a tentative track satisfies the configured confirmation rule.

```json
{
  "track_id": 7,
  "visit_session_id": "visit_7_123456789",
  "state": "confirmed",
  "visible": true,
  "bbox": {"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.7},
  "area_ratio": 0.21,
  "primary": false
}
```

### `track_recovered`

Emitted when a lost track is associated with a new detection before the lost timeout.

```json
{"track_id": 7, "lost_seconds": 1.15}
```

### `visitor_engaged`

Emitted once per track when its bottom-center is inside the engagement polygon and its body area
satisfies the engagement threshold for the configured number of frames.

### `visitor_left`

Emitted after a confirmed track remains lost beyond the recovery timeout. The same person may
receive a new `track_id` after returning because persistent identity is outside Phase 2.

## Scene and selection events

### `group_detected`

```json
{"person_count": 3, "track_ids": [4, 7, 8]}
```

Emitted on the transition from fewer than two visible confirmed tracks to two or more.

### `primary_visitor_changed`

```json
{
  "previous_track_id": 7,
  "current_track_id": 8,
  "reason": "previous_lost_or_challenged",
  "previous_score": 0.51,
  "current_score": 0.79
}
```

Primary selection uses a stable-acquisition delay, challenger score margin, challenger hold time,
and a short lost-track lock.

## Current state endpoint

```text
GET /api/v1/tracks
```

Returns scene state, visible person count, active tracks, primary track, association diagnostics,
and the active appearance backend.

Track states:

```text
tentative
confirmed
lost
removed
```

`removed` tracks are retained internally only for a short diagnostic interval and are not returned
as active tracks.
