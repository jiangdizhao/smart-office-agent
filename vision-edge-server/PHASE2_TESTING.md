# Phase 2 multi-object tracking test guide

Phase 2 keeps the verified Phase 1 camera and YOLOX detector, then adds:

- XYWH constant-velocity Kalman prediction;
- ByteTrack-style high- and low-confidence association;
- Hungarian minimum-cost assignment;
- appearance-assisted recovery using OSNet ONNX when available;
- an explicitly reported HSV-histogram fallback before OSNet is exported;
- tentative, confirmed, lost, recovered, and removed track lifecycle;
- two-second short-occlusion recovery window;
- track trails, stable `track_id`, and primary-visitor hysteresis;
- `visitor_entered`, `visitor_engaged`, `track_recovered`, `visitor_left`,
  `group_detected`, and `primary_visitor_changed` events.

## 1. Pull the branch

```powershell
cd D:\smart-office-agent
git switch Vision-Edge-Server
git pull --ff-only origin Vision-Edge-Server
git rev-parse --short HEAD
```

## 2. Optional but recommended: export OSNet x0.25 ONNX

The official model repository publishes the x0.25 ReID weights in PyTorch format. This script
creates a separate exporter environment and writes the ONNX model without installing PyTorch in
`smartoffice`:

```powershell
cd D:\smart-office-agent\vision-edge-server
.\scripts\export_osnet_x0_25_onnx.ps1
Get-Item .\models\osnet_x0_25.onnx
```

Without this file, the server intentionally reports:

```text
appearance=hsv_histogram
```

That fallback allows functional tracking tests, but final occlusion-recovery accuracy should be
judged with:

```text
appearance=osnet_onnx
```

## 3. Unit tests

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
python -m pytest -q
```

## 4. Start the server

Close Camera, Teams, and other camera users first.

```powershell
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Keep this terminal open.

## 5. Automated Phase 1 + Phase 2 smoke test

Open a second terminal:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\run_phase2_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

After exporting OSNet, use the strict check:

```powershell
.\scripts\run_phase2_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -RequireOsnet
```

Expected first line:

```text
PASS: Phase 1 detection and Phase 2 tracking pipeline is ready.
```

## 6. Compact live evaluation window

```powershell
.\scripts\run_phase12_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -WindowWidth 960 `
  -WindowHeight 540
```

Window controls:

```text
Q or Esc  quit
P         pause/resume
Space     save the current annotated screenshot
R         restart the server-side vision pipeline
```

The view shows:

- green confirmed tracks;
- cyan tentative tracks;
- orange predicted boxes during short loss;
- magenta primary visitor;
- track trails;
- track ID, state, detector confidence, body-area ratio, and engagement state;
- active appearance backend and tracker processing time.

The viewer records each observation under:

```text
logs/phase12_live_view/*.jsonl
```

## 7. Manual scenarios

### Single visitor stability

Stay visible and move around for two minutes. The same person should retain one `track_id`.

### Partial occlusion

Move behind a chair or another person while part of the body remains visible. Low-confidence
ByteTrack association should retain the ID.

### Full short occlusion

Have another person completely cover the subject for approximately 0.5, 1.0, and 1.5 seconds.
The track should become orange/lost and then return with the original ID. A `track_recovered`
event should be emitted.

### Timeout boundary

Remain fully absent for more than two seconds. The old track is allowed to emit `visitor_left`;
a later appearance may receive a new ID because Phase 2 is anonymous short-term tracking rather
than persistent identity recognition.

### Crossing people

Two visitors cross paths five to ten times. Check whether IDs swap after overlap. Run this test
with OSNet active before judging final appearance-assisted recovery.

### Primary visitor hysteresis

Let one visitor become primary, then have another visitor walk briefly closer. The primary should
not switch immediately. It should change only after the challenger remains materially better for
the configured hold interval or the previous primary exceeds its lost lock.

## 8. JSON inspection

```text
http://127.0.0.1:8015/api/v1/status
http://127.0.0.1:8015/api/v1/detections
http://127.0.0.1:8015/api/v1/tracks
http://127.0.0.1:8015/api/v1/debug/stream.mjpg
```

Important fields:

```text
vision.tracking.appearance.backend
vision.tracking.last_association
vision.tracking.primary_track_id
vision.tracking.tracks[].track_id
vision.tracking.tracks[].state
vision.tracking.tracks[].recovered_count
vision.last_timings.tracking_ms
```

## 9. Initial tuning controls

Edit `config/vision.yaml`, then restart the server:

```text
tracking.high_threshold
tracking.new_track_threshold
tracking.lost_timeout_seconds
tracking.appearance_similarity_gate
tracking.first_stage_max_cost
primary.challenger_margin
primary.challenger_hold_seconds
presence.engagement_min_area_ratio
```

Do not change several association thresholds at once. Save the JSONL observation log and test one
controlled scenario after each change.
