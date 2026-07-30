# Vision Event Protocol v3

Protocol v3 adds a stable exhibition-visit layer between short-lived tracking and persistent,
consented identity. The WebSocket endpoint remains:

```text
ws://<vision-server>:8015/ws/v1/events
```

The wire envelope remains protocol version `1.0` for backward compatibility:

```json
{
  "protocol_version": "1.0",
  "event_id": "evt_<uuid>",
  "sequence": 1,
  "server_time": "2026-07-31T00:00:00+00:00",
  "source": "rtx-vision-edge-server",
  "type": "visitor_session_recovered",
  "payload": {}
}
```

Clients must ignore event types they do not consume. Reconnecting clients should request a fresh
`state_snapshot` because event delivery is live and not replayed.

## Identifiers

```text
track_id             One continuous multi-object-tracking trajectory.
visitor_session_id   One memory-only exhibition visit, recoverable for the configured TTL.
identity_id          One persistent local identity created after explicit consent.
```

Consumers must not assume that `track_id` remains unchanged after a person fully leaves the camera
view. The continuity key for an ongoing visit is `visitor_session_id`.

## Visitor-session events

### `visitor_session_started`

```json
{
  "visitor_session_id": "visitor_2f9e8b0193a1",
  "track_id": 17
}
```

### `visitor_session_recovered`

Emitted when a new track is linked to a prior visitor session.

```json
{
  "visitor_session_id": "visitor_2f9e8b0193a1",
  "previous_track_id": 17,
  "current_track_id": 24,
  "reason": "face_high",
  "face_similarity": 0.68,
  "face_margin": 0.21,
  "body_similarity": 0.83,
  "age_seconds": 6.4
}
```

Possible recovery reasons:

```text
registered_identity
face_high
face_body_medium
body_only
```

`face_body_medium` and `body_only` require repeated evidence. `registered_identity` and a clear
high-confidence face match may recover immediately.

### `visitor_session_identified`

```json
{
  "visitor_session_id": "visitor_2f9e8b0193a1",
  "track_id": 24,
  "identity_id": "person_...",
  "display_name": "Rico"
}
```

### `visitor_session_expired`

```json
{
  "visitor_session_id": "visitor_2f9e8b0193a1",
  "last_track_id": 24,
  "identity_id": "person_..."
}
```

Anonymous face and body embeddings are deleted from memory when the session expires.

## Face events

### `visitor_face_recognition_usable`

The current face is sufficient to attempt a comparison against the existing identity gallery. It
may still be below the stricter enrollment gate.

### `visitor_face_ready`

The current track has accumulated enough enrollment-quality observations.

### `visitor_face_lost`

The track no longer has a recent face observation. The person track or visitor session may still
exist.

## Identity events

### `visitor_identified`

Emitted only after the configured adaptive confirmation rule is satisfied. Representative payload:

```json
{
  "track_id": 24,
  "visitor_session_id": "visitor_2f9e8b0193a1",
  "identity_id": "person_...",
  "display_name": "Rico",
  "similarity": 0.64,
  "sample_similarities": [0.69, 0.64, 0.59],
  "second_best_similarity": 0.19,
  "margin": 0.45,
  "decision_tier": "high",
  "required_observations": 1,
  "confirmed_observations": 1,
  "source": "sface_gallery"
}
```

### `identity_enrolled`

```json
{
  "track_id": 24,
  "visitor_session_id": "visitor_2f9e8b0193a1",
  "identity_id": "person_...",
  "display_name": "Rico",
  "samples_added": 4,
  "reused_existing_identity": true
}
```

### `identity_deleted`

```json
{
  "identity_id": "person_..."
}
```

## Existing tracking and reception events

The following remain supported and are enriched with `visitor_session_id` when a corresponding
session exists:

```text
visitor_entered
visitor_engaged
track_recovered
visitor_left
group_detected
primary_visitor_changed
```

Some legacy tracker payloads may also contain `visit_session_id`. New clients must use
`visitor_session_id`, which is the stable fusion-layer identifier.

## Privacy boundary

- Anonymous visitor-session face and body embeddings are memory-only.
- Anonymous visitor sessions are not restored after server restart.
- No face images are stored by the identity runtime.
- Persistent identity enrollment requires explicit consent.
- Identity enrollment and deletion endpoints are restricted to the local RTX operator.
- A local SFace match is not legal or government identity verification.
