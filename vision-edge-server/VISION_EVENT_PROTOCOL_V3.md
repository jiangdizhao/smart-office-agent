# Vision event protocol: Phase 3 and Phase 4 additions

The WebSocket endpoint remains:

```text
ws://<vision-server>:8015/ws/v1/events
```

The wire envelope remains protocol version `1.0` for backward compatibility:

```json
{
  "protocol_version": "1.0",
  "event_id": "evt_<uuid>",
  "sequence": 1,
  "server_time": "2026-07-30T10:00:00+00:00",
  "source": "rtx-vision-edge-server",
  "type": "visitor_face_ready",
  "payload": {}
}
```

Clients must ignore event types they do not consume. Reconnect clients should request a fresh
`state_snapshot` because event delivery is live and not replayed.

## Existing person and tracking events

```text
visitor_entered
visitor_engaged
track_recovered
visitor_left
group_detected
primary_visitor_changed
```

Those event payloads continue to carry the anonymous `track_id`.

## Phase 3 events

### `visitor_face_ready`

Emitted when a track has passed the face quality gate for the configured number of stable face
analysis cycles.

Representative payload:

```json
{
  "track_id": 7,
  "frame_id": 2810,
  "bbox": {
    "x": 0.42,
    "y": 0.18,
    "width": 0.12,
    "height": 0.22
  },
  "landmarks": [
    {"x": 0.45, "y": 0.25},
    {"x": 0.50, "y": 0.25},
    {"x": 0.48, "y": 0.29},
    {"x": 0.46, "y": 0.34},
    {"x": 0.50, "y": 0.34}
  ],
  "quality": {
    "score": 0.82,
    "ready": true,
    "sharpness": 120.0,
    "brightness": 118.0,
    "frontal_score": 0.88
  },
  "stable_frames": 3,
  "ready": true,
  "primary": true
}
```

The event means the image is suitable for the configured identity pipeline. It is not a
liveness or legal-identity assertion.

### `visitor_face_lost`

Emitted when a previously ready face has not been observed for the configured stale timeout.
The person track may still exist.

```json
{
  "track_id": 7,
  "age_seconds": 1.04
}
```

## Phase 4 events

### `visitor_identified`

Emitted once when a track is confirmed against a consented local identity.

```json
{
  "track_id": 12,
  "identity_id": "person_1234",
  "display_name": "Rico",
  "external_id": null,
  "similarity": 0.71,
  "second_best_similarity": 0.44,
  "margin": 0.27,
  "quality_score": 0.84,
  "confirmed_observations": 3,
  "identified_at_unix": 1785399000.0,
  "consent_at_unix": 1785398000.0
}
```

Consumers should use `identity_id` as the stable local key and `track_id` as the current visual
session key.

### `identity_enrolled`

Emitted only after an explicit enrollment API action.

```json
{
  "track_id": 7,
  "identity_id": "person_1234",
  "display_name": "Rico",
  "consent_at_unix": 1785398000.0
}
```

### `identity_deleted`

```json
{
  "identity_id": "person_1234"
}
```

After deletion, the corresponding embeddings are removed by SQLite foreign-key cascade and the
in-memory gallery is reloaded.

## Privacy boundary

- Face images are not stored by the identity database.
- Enrollment is disabled without explicit consent.
- Embeddings and consent metadata stay on the local RTX vision server.
- `visitor_identified` describes a match to a local enrolled identity, not a government or legal
  identity verification.
