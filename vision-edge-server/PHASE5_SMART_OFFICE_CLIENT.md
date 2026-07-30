# Phase 5 — Smart Office Client integration

## Runtime split

- RTX 3070 Ti laptop: branch `Vision-Edge-Server`, camera and Phase 0–4 vision service.
- i5 Smart Office laptop: branch `integration/virtual-host`, Virtual Host, GPT Realtime and Office control.
- Transport: HTTP and WebSocket on the exhibition LAN, TCP port `8015`.

The two Git branches are intentionally not merged. They communicate through the versioned LAN contract.

## Endpoints

```text
GET  http://<RTX-IP>:8015/health
GET  http://<RTX-IP>:8015/api/v1/client/state
WS   ws://<RTX-IP>:8015/ws/v1/events
```

`/api/v1/client/state` and the WebSocket event `client_state_snapshot` use compact schema
`phase5.1`. The payload contains only the state needed by the Smart Office client:

- service readiness;
- scene state and person count;
- current Primary visitor;
- `track_id`, `visitor_session_id`, body area/confidence and center;
- face presence, confidence, frontal score and quality gates;
- consented local identity when available;
- `greeting_eligible`.

`greeting_eligible=true` means the current visitor is visible, confirmed, Primary, engaged,
has a visitor session and has a detected face. It is only a visual candidate. The i5 client still
checks conversation, voice, task and recording state before greeting.

## WebSocket client messages

```json
{"type":"ping","client_time":"2026-07-31T07:00:00+10:00"}
{"type":"get_client_state"}
```

Responses are normal event envelopes with `event_id`, `sequence`, `server_time`, `type` and
`payload`. Existing `get_state` remains supported for backward compatibility.

## LAN validation

Run on either computer after the RTX service has started:

```powershell
.\scripts\run_phase5_lan_client_test.ps1 `
  -Server "http://192.168.1.50:8015" `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

The test verifies HTTP health, compact HTTP state, WebSocket connection and
`client_state_snapshot` schema.

## Network requirements

- Give the RTX laptop a DHCP reservation or fixed private IPv4 address.
- Allow inbound TCP `8015` on the RTX laptop only for the Private network profile or the local
  exhibition subnet.
- Do not expose port `8015` to the public internet.
- Use `ws://` when the i5 UI is served over HTTP. A future HTTPS deployment must use `wss://`.

## Greeting semantics

The i5 client deduplicates greetings by `visitor_session_id`, not `track_id`. A new low-level
track after a short exit therefore does not cause a second greeting when Phase 4 restores the
same visitor session.

The browser MediaPipe implementation remains available only as an explicitly configured fallback.
Production exhibition configuration should use remote RTX vision without silent fallback because
the browser camera and RTX camera may observe different scenes.
