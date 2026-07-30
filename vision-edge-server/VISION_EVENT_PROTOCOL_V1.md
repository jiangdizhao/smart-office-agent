# Vision Event Protocol v1

Phase 0 establishes the transport contract only. Person, face, tracking, and visitor events are added in later phases.

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
  "type": "server_ready",
  "payload": {}
}
```

`sequence` is monotonic for one server process. Clients should use `event_id` for duplicate protection and request a fresh `state_snapshot` after reconnecting.

## Phase 0 server events

- `server_ready`: connection accepted and protocol information available.
- `state_snapshot`: current hardware and runtime state.
- `heartbeat`: periodic liveness event.
- `probe_completed`: manual or startup hardware probe finished.
- `pong`: response to a client `ping`.
- `client_error`: malformed or unsupported client message.

## Phase 0 client messages

Ping:

```json
{"type": "ping", "client_time": "optional-client-value"}
```

Request current state:

```json
{"type": "get_state"}
```

## Future events

The protocol reserves later event names including `visitor_entered`, `visitor_engaged`, `visitor_identified`, `primary_visitor_changed`, `visitor_left`, and `group_detected`. Phase 0 does not emit them.
