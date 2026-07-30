# Vision Event Protocol v1

Phase 1 keeps the Phase 0 transport envelope and adds aggregate person-presence events. Stable per-person tracking IDs are intentionally deferred to Phase 2.

## Endpoint

```text
ws://<vision-server>:8015/ws/v1/events
```

## Envelope

Every server event uses this shape:

```json
{
  "protocol_version": "1.0",
  "event_id": "evt_<uuid>",
  "sequence": 1,
  "server_time": "2026-07-30T05:00:00+00:00",
  "source": "rtx-vision-edge-server",
  "type": "visitor_entered",
  "payload": {}
}
```

`sequence` is monotonic for one server process. Clients should use `event_id` for duplicate protection and request a fresh `state_snapshot` after reconnecting.

## System events

- `server_ready`: connection accepted and protocol information available.
- `state_snapshot`: current hardware, camera, detector, and presence state.
- `heartbeat`: periodic liveness event including the current vision status.
- `probe_completed`: manual or startup hardware probe finished.
- `server_degraded`: a runtime component such as the detector could not start.
- `pong`: response to a client `ping`.
- `client_error`: malformed or unsupported client message.

## Phase 1 visitor events

### `visitor_entered`

Emitted after a person has been detected for the configured confirmation-frame count.

```json
{
  "visit_session_id": "visit_<uuid>",
  "person_count": 1,
  "detections": []
}
```

### `visitor_engaged`

Emitted after a detected person meets both the configured engagement-zone and minimum-area requirements for the configured confirmation-frame count.

```json
{
  "visit_session_id": "visit_<uuid>",
  "person_count": 1,
  "primary_detection": {
    "score": 0.91,
    "area_ratio": 0.18,
    "bbox": {"x": 0.3, "y": 0.1, "width": 0.4, "height": 0.7}
  }
}
```

### `visitor_left`

Emitted after no person is detected for `presence.left_timeout_seconds`.

```json
{
  "visit_session_id": "visit_<uuid>",
  "duration_seconds": 42.5,
  "absence_seconds": 1.5
}
```

### `group_detected`

Emitted when the aggregate person count changes from zero or one to more than one.

```json
{
  "visit_session_id": "visit_<uuid>",
  "person_count": 3
}
```

## Client messages

Ping:

```json
{"type": "ping", "client_time": "optional-client-value"}
```

Request current state:

```json
{"type": "get_state"}
```

## Phase 1 limitation

The event stream is aggregate-presence based. Detection boxes do not yet carry stable `track_id` values. Phase 2 will add multi-object tracking, primary-visitor locking, and `primary_visitor_changed` events. Face identity remains a later phase.
