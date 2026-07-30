# RTX Vision Edge Server

Standalone Windows vision service on branch `Vision-Edge-Server`. It is isolated from the inherited Smart Office task runtime and is intended for the RTX 3070 Ti laptop with USB camera index `1`.

## Current milestone

```text
version: 0.3.0
phase: phase2_multi_object_tracking
```

Phase 0 established hardware probing and sustained camera benchmarking. Phase 1 established the verified live path:

```text
camera 1 + MSMF + AUTO + 3840×2160 @ 30 FPS
→ latest-frame buffer
→ 960×540 global frame
→ YOLOX-Nano 416×416
→ CUDAExecutionProvider
```

Phase 2 adds anonymous multi-object tracking and primary-visitor selection:

- XYWH constant-velocity Kalman filter;
- ByteTrack-style high/low-confidence association;
- Hungarian minimum-cost assignment;
- appearance-assisted matching with OSNet x0.25 ONNX when available;
- explicit HSV-histogram fallback for initial functional testing;
- tentative, confirmed, lost, recovered, and removed track lifecycle;
- two-second short-occlusion recovery window;
- lightweight observation-centric velocity correction after recovery;
- stable `track_id` values, trails, engagement state, and primary hysteresis;
- `visitor_entered`, `visitor_engaged`, `track_recovered`, `visitor_left`, `group_detected`, and `primary_visitor_changed` events;
- compact OpenCV evaluation window with detector boxes and track overlays.

Phase 2 does **not** perform face recognition or persistent human identity. A visitor returning after the lost timeout may receive a new `track_id`.

## Environment

Use the verified `smartoffice` environment:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
python -m pip check
```

Important pinned dependencies:

```text
opencv-python==4.9.0.80
numpy==1.26.4
protobuf==4.25.9
onnxruntime-gpu[cuda,cudnn]==1.28.0
```

PowerShell launchers resolve Python in this order:

1. explicit `-PythonExe`;
2. `$env:CONDA_PREFIX\python.exe`;
3. `python` from `PATH`.

## Models

YOLOX-Nano:

```powershell
.\scripts\download_yolox_nano.ps1
```

Expected file:

```text
models/yolox_nano.onnx
```

Recommended Phase 2 OSNet x0.25 ONNX export:

```powershell
.\scripts\export_osnet_x0_25_onnx.ps1
```

Expected file:

```text
models/osnet_x0_25.onnx
```

The exporter creates a separate `visionedge-osnet-export` Conda environment. PyTorch is not installed into `smartoffice`.

When the OSNet file is absent, the server reports:

```text
appearance.backend = hsv_histogram
```

This fallback is useful for functional tests but is not equivalent to neural ReID. Final crossing and occlusion tests should use:

```text
appearance.backend = osnet_onnx
```

## Unit tests

```powershell
python -m pytest -q
```

## Start the service

Close Camera, Teams, and any other camera user first:

```powershell
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

## Automated Phase 1 + Phase 2 smoke test

Open a second terminal:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\run_phase2_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Strict neural-ReID check after exporting OSNet:

```powershell
.\scripts\run_phase2_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -RequireOsnet
```

## Compact live evaluation window

```powershell
.\scripts\run_phase12_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -WindowWidth 960 `
  -WindowHeight 540
```

Controls:

```text
Q / Esc   quit
P         pause or resume
Space     save annotated screenshot
R         restart server-side vision pipeline
```

Overlay conventions:

```text
thin grey box    raw YOLOX detection
green box        confirmed track
cyan box         tentative track
orange box       temporarily lost/predicted track
magenta box      primary visitor
coloured line    track trail
```

The viewer writes frame-by-frame diagnostic JSONL under:

```text
logs/phase12_live_view/
```

Detailed controlled test procedures are in [`PHASE2_TESTING.md`](./PHASE2_TESTING.md).

## Main endpoints

```text
GET  /health
GET  /api/v1/status
GET  /api/v1/config/public
GET  /api/v1/detections
GET  /api/v1/tracks
GET  /api/v1/debug/frame.jpg
GET  /api/v1/debug/stream.mjpg
POST /api/v1/vision/start
POST /api/v1/vision/stop
POST /api/v1/vision/restart
POST /api/v1/probe
WS   /ws/v1/events
```

Interactive API documentation:

```text
http://127.0.0.1:8015/docs
```

## Existing Phase 0 tools

Hardware probe:

```powershell
.\scripts\probe_hardware.ps1 -Mode all
```

Five-minute camera benchmark:

```powershell
.\scripts\run_camera_benchmark.ps1 `
  -DurationSeconds 300 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Stop the live vision pipeline before running a camera probe because both operations own camera index `1`.

## Network access from the i5 client

```powershell
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/health
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/api/v1/tracks
```

Debug stream:

```text
http://<RTX-LAPTOP-IP>:8015/api/v1/debug/stream.mjpg
```

Restrict any Windows Firewall inbound rule for TCP port `8015` to the exhibition LAN profile.
