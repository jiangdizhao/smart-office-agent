# Phase 5 — i5 Remote Vision Client

This branch runs on the i5 Smart Office computer. It no longer starts browser MediaPipe as the
normal proximity path. The default source is the RTX vision service on the same LAN.

## Configuration

Copy the example into the Vite UI directory:

```powershell
cd D:\smart-office-agent\ui\smart-office-ui
Copy-Item .env.phase5.example .env.local
notepad .env.local
```

Set the RTX laptop address:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_VISION_SOURCE=remote
VITE_VISION_SERVER_HTTP=http://192.168.1.50:8015
VITE_VISION_SERVER_WS=ws://192.168.1.50:8015/ws/v1/events
enable_greet=true
```

Recommended exhibition mode:

```env
VITE_VISION_SOURCE=remote
```

Development-only explicit fallback:

```env
VITE_VISION_SOURCE=remote-with-fallback
```

Direct browser MediaPipe mode remains available for isolated testing:

```env
VITE_VISION_SOURCE=mediapipe
```

## Runtime behavior

`RemoteVisionClient`:

- connects to `/ws/v1/events`;
- requests compact `client_state_snapshot` every 750 ms;
- sends heartbeat pings;
- reconnects with bounded exponential backoff;
- deduplicates event IDs;
- requests fresh state after engagement, identity, session recovery and Primary changes;
- converts the RTX Primary visitor into the existing proximity detection contract.

`useProximityGreeting` keeps the existing Smart Office eligibility checks. A greeting occurs only
when the Agent is in standby/idle, not listening, not speaking, not executing a task and not
recording. Remote greetings are deduplicated by `visitor_session_id`; a changed `track_id` alone
cannot cause another greeting.

## Pre-start LAN test

From the i5 computer:

```powershell
conda activate sm
cd D:\smart-office-agent\backend

.\scripts\test_phase5_remote_vision_lan.ps1 `
  -Server "http://192.168.1.50:8015" `
  -PythonExe "D:\anaconda3\envs\sm\python.exe"
```

Expected:

```text
PASS: i5 client can reach the RTX Phase 5 vision service over HTTP and WebSocket.
```

## Contract and build checks

```powershell
cd D:\smart-office-agent
python .\backend\scripts\smoke_phase5_remote_vision_contract.py

cd .\ui\smart-office-ui
npm install
npm run build
```

## Operator Drawer

The proximity section now displays:

- remote/MediaPipe source;
- connecting, connected, reconnecting, offline or fallback status;
- configured WebSocket endpoint;
- latest person area and face-present state.

## Failure policy

In production `remote` mode, a disconnected RTX service does not silently activate the i5 browser
camera. This prevents two cameras observing different scenes and causing duplicate or unexplained
greetings. Restore the RTX service or deliberately change the source mode.
