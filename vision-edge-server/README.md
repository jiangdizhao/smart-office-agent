# RTX Vision Edge Server

Standalone Windows vision service on branch `Vision-Edge-Server`. It is isolated from the
inherited Smart Office task runtime and is intended for the RTX 3070 Ti laptop with USB camera
index `1`.

## Current milestone

```text
version: 0.5.0
phase: phase4_face_identity
```

The complete local pipeline is:

```text
camera 1 + MSMF + AUTO + 3840×2160 @ 30 FPS
→ latest-frame buffer
→ 960×540 YOLOX-Nano person detection on ONNX Runtime CUDA
→ Phase 2 anonymous tracking and Primary selection
→ Phase 3 track-linked YuNet face detection and quality
→ Phase 4 consent-based local SFace identity
```

## Implemented phases

### Phase 0: hardware foundation

- GPU and camera probes;
- actual camera-mode measurement;
- five-minute sustained 4K capture benchmark;
- FastAPI and WebSocket service foundation.

### Phase 1: person detection

- dedicated MSMF camera thread;
- latest-frame replacement rather than an inference backlog;
- verified 4K30 capture;
- 960×540 global frame;
- YOLOX-Nano person detection;
- CUDAExecutionProvider verification;
- annotated debug JPEG and MJPEG stream.

### Phase 2: anonymous multi-object tracking

- XYWH constant-velocity Kalman filter;
- ByteTrack-style high/low-confidence association;
- Hungarian minimum-cost assignment;
- OSNet x0.25 appearance evidence;
- tentative, confirmed, lost, recovered and removed lifecycle;
- two-second short-occlusion recovery;
- observation-centric velocity correction after recovery;
- stable `track_id`, track trails, engagement and Primary hysteresis;
- visitor, group and Primary events.

The user has already passed Phase 2 unit tests, strict OSNet smoke testing, single-person
stability, short complete occlusion and timeout-boundary tests. Two-person crossing and Primary
hysteresis remain deferred until multiple participants are available.

### Phase 3: face detection and quality

- YuNet face detection inside the high-resolution 4K person ROI;
- face box and five landmarks linked to `track_id`;
- Primary and larger tracks prioritized at a configurable 5 Hz;
- face confidence, pixel size, sharpness, brightness, roll and frontal score;
- stable quality gate before identity processing;
- `visitor_face_ready` and `visitor_face_lost` events.

### Phase 4: consent-based local identity

- SFace aligned face embedding;
- cosine matching with best-versus-second-best margin;
- three accepted observations before confirmation;
- local SQLite identity gallery;
- explicit-consent enrollment only;
- no automatic enrollment and no stored face photographs;
- bounded embedding samples per identity;
- identity listing and deletion;
- `visitor_identified`, `identity_enrolled` and `identity_deleted` events.

Phase 4 is a local exhibition identity capability, not a claim of legal identity, liveness
verification, demographic inference or unrestricted surveillance.

## Environment

Use the verified `smartoffice` environment:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
python -m pip check
```

Pinned compatibility dependencies include:

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

OSNet x0.25 ONNX export:

```powershell
.\scripts\export_osnet_x0_25_onnx.ps1
```

YuNet and SFace:

```powershell
.\scripts\download_face_models.ps1
```

Expected files:

```text
models/yolox_nano.onnx
models/osnet_x0_25.onnx
models/face_detection_yunet_2023mar.onnx
models/face_recognition_sface_2021dec.onnx
```

PyTorch remains confined to the separate OSNet exporter environment. The working
`smartoffice` runtime uses OpenCV and ONNX Runtime.

## Unit tests

```powershell
python -m pytest -q
```

## Start the service

Close Camera, Teams and every other camera user first:

```powershell
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Expected service identity:

```text
version = 0.5.0
phase = phase4_face_identity
```

## Automated validation

Phase 1 and Phase 2:

```powershell
.\scripts\run_phase2_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -RequireOsnet
```

Phase 3 and Phase 4 model/runtime check:

```powershell
.\scripts\run_phase34_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Strict visible-face check:

```powershell
.\scripts\run_phase34_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -RequireFace
```

## Compact Phase 1-4 live evaluation window

```powershell
.\scripts\run_phase1234_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -WindowWidth 960 `
  -WindowHeight 540
```

Controls:

```text
Q / Esc   quit
P         pause or resume
Space     save annotated screenshot
R         restart the server-side vision pipeline
L         print enrolled identities
E         enroll the current Primary when -EnrollName was supplied
```

To make operator enrollment available after explicit participant consent:

```powershell
.\scripts\run_phase1234_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -EnrollName "Rico"
```

Pressing `E` is rejected unless the Primary has a stable high-quality face and a current
SFace embedding.

The viewer writes JSONL diagnostics and screenshots under:

```text
logs/phase1234_live_view/
```

Detailed procedures:

- [`PHASE2_TESTING.md`](./PHASE2_TESTING.md)
- [`PHASE3_4_TESTING.md`](./PHASE3_4_TESTING.md)

## Identity management

List:

```powershell
.\scripts\manage_identity.ps1 -Action list
```

Enroll the current consented track:

```powershell
.\scripts\manage_identity.ps1 `
  -Action enroll `
  -TrackId 1 `
  -DisplayName "Rico" `
  -Consent
```

Delete:

```powershell
.\scripts\manage_identity.ps1 `
  -Action delete `
  -IdentityId "person_replace_with_actual_id"
```

Local database:

```text
data/identity/visitor_identities.sqlite3
```

The `data/`, `logs/` and ONNX model paths are ignored by Git.

## Main endpoints

```text
GET    /health
GET    /api/v1/status
GET    /api/v1/config/public
GET    /api/v1/detections
GET    /api/v1/tracks
GET    /api/v1/faces
GET    /api/v1/identities
POST   /api/v1/identities/enroll
DELETE /api/v1/identities/{identity_id}
GET    /api/v1/debug/frame.jpg
GET    /api/v1/debug/stream.mjpg
POST   /api/v1/vision/start
POST   /api/v1/vision/stop
POST   /api/v1/vision/restart
POST   /api/v1/probe
WS     /ws/v1/events
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

Stop the live pipeline before running a camera probe because both operations own camera index
`1`.

## Network access from the i5 client

```powershell
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/health
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/api/v1/tracks
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/api/v1/faces
```

Debug stream:

```text
http://<RTX-LAPTOP-IP>:8015/api/v1/debug/stream.mjpg
```

Restrict any Windows Firewall inbound rule for TCP port `8015` to the exhibition LAN profile.
