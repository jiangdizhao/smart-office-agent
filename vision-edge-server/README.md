# RTX Vision Edge Server

Standalone Windows vision service on branch `Vision-Edge-Server`. It is isolated from the inherited
Smart Office task runtime and is intended for the RTX 3070 Ti laptop with USB camera index `1`.

## Current milestone

```text
version: 0.6.0
phase: phase4_identity_session_fusion
```

The complete local pipeline is:

```text
camera 1 + MSMF + AUTO + 3840×2160 @ 30 FPS
→ latest-frame buffer
→ 960×540 YOLOX-Nano person detection on ONNX Runtime CUDA
→ ByteTrack-style / Kalman / OSNet anonymous tracking
→ YuNet face detection inside the matching 4K person ROI
→ SFace recognition and consent-based local enrollment
→ memory-only visitor-session fusion across changing track IDs
```

## Identifier model

The service deliberately exposes three different identifiers:

```text
track_id             one continuous MOT trajectory; it may change after leaving the frame
visitor_session_id   one exhibition visit; recoverable across new track IDs for 60 seconds
identity_id          persistent, consented local identity such as Rico
```

A person can therefore return as a new `track_id` while retaining the same `visitor_session_id` and,
when enrolled, the same `identity_id` and display name. Reusing a low-level `track_id` after a true
exit is not required and would be unsafe in multi-person scenes.

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
- OSNet x0.25 body-appearance evidence;
- tentative, confirmed, lost, recovered and removed lifecycle;
- two-second short-occlusion recovery;
- observation-centric velocity correction after recovery;
- per-track trails, engagement and Primary hysteresis.

The user has passed Phase 2 unit tests, strict OSNet smoke testing, single-person stability, short
complete occlusion and timeout-boundary tests. Two-person crossing and Primary hysteresis remain
deferred until multiple participants are available.

### Phase 3: face detection and two quality gates

- YuNet face detection inside the high-resolution 4K person ROI;
- face box and five landmarks linked to `track_id`;
- up to four visible tracks prioritized at 7.5 Hz;
- face confidence, pixel size, sharpness, brightness, roll and frontal score;
- a permissive **recognition-usable** gate for comparing against an existing gallery;
- a stricter **enrollment-usable** gate for writing consented samples;
- explicit rejection reasons such as `sharpness 18.0 < 22.0` in the debug overlay.

Recognition no longer waits for the stricter enrollment gate.

### Phase 4: fast multi-sample identity

- SFace aligned face embeddings;
- per-identity, per-sample comparison rather than a single averaged prototype;
- quality-weighted top-three sample score;
- adaptive confirmation:
  - high-confidence match: one observation;
  - medium-confidence match: two observations;
  - low-confidence match: three observations;
- display-name grouping so duplicate `Rico` database rows do not compete against each other in the
  best-versus-second-best margin;
- sticky identity for the active visitor session after a reliable confirmation;
- explicit-consent enrollment only;
- three-second enrollment capture using several high-quality, non-identical samples;
- re-enrolling a recognized person or the same display name adds samples to the existing identity;
- local SQLite gallery, bounded samples, identity listing and deletion;
- no stored face photographs.

### Visitor-session fusion

`VisitorSessionRuntime` keeps anonymous face and body embeddings in memory only:

```text
TTL: 60 seconds
maximum face embeddings per session: 8
maximum body embeddings per session: 8
```

Recovery evidence is ordered from strongest to weakest:

1. same registered `identity_id`;
2. high-confidence SFace similarity;
3. repeated medium SFace evidence combined with OSNet body or spatial evidence;
4. repeated body-only evidence for a short return interval.

A young provisional session can be replaced after SFace becomes available, preventing a new track
from becoming permanently detached merely because its first confirmed frame did not yet contain a
usable face.

Events include:

```text
visitor_session_started
visitor_session_recovered
visitor_session_identified
visitor_session_expired
visitor_identified
identity_enrolled
identity_deleted
```

Anonymous session embeddings are not written to SQLite and disappear when the service restarts or
the session TTL expires.

## Environment

Use the verified environment:

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

1. explicitly supplied `-PythonExe`;
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

PyTorch remains confined to the separate OSNet exporter environment. The `smartoffice` runtime uses
OpenCV and ONNX Runtime.

## Pull and test

```powershell
cd D:\smart-office-agent
git switch Vision-Edge-Server
git pull --ff-only origin Vision-Edge-Server
git rev-parse --short HEAD

conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
python -m pytest -q
```

The unit suite now covers:

- Phase 0-4 contracts;
- recognition versus enrollment quality gates;
- adaptive immediate high-confidence recognition;
- duplicate-name pooling;
- multi-sample enrollment and same-name reuse;
- face-based visitor-session recovery across new tracks;
- delayed-face provisional-session rebinding;
- identity propagation across track changes.

## Start the service

Close Camera, Teams and every other camera user first:

```powershell
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Expected service identity:

```text
version = 0.6.0
phase = phase4_identity_session_fusion
```

## Automated live smoke test

Open a second PowerShell:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server

.\scripts\run_phase1234_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Strict visible-face recognition-input check:

```powershell
.\scripts\run_phase1234_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -RequireFace
```

`-RequireFace` now requires a recognition-usable face, not the stricter enrollment-usable state.

## Compact live evaluation window

```powershell
.\scripts\run_phase1234_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -WindowWidth 960 `
  -WindowHeight 540
```

The overlay distinguishes:

```text
T=<number>          low-level track_id
V=<short code>      stable visitor_session_id
NAME=<name>         persistent identity when recognized
rec=Y/N             usable for recognition
enroll=Y/N          usable for enrollment
```

Controls:

```text
Q / Esc   quit
P         pause or resume
Space     save annotated screenshot
R         restart the server-side vision pipeline
L         print enrolled identities
E         start consented multi-sample enrollment when -EnrollName is supplied
```

To enable operator enrollment after explicit participant consent:

```powershell
.\scripts\run_phase1234_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -WindowWidth 960 `
  -WindowHeight 540 `
  -EnrollName "Rico"
```

Press `E`, look toward the camera for about three seconds, and wait for output such as:

```text
Enrollment complete: Rico -> person_...; samples_added=3; capture=3.0s;
reused_existing=True
```

When the current track is already recognized, or a case-insensitive `Rico` entry already exists,
samples are appended to that identity instead of creating another competing person.

The viewer writes JSONL diagnostics and screenshots under:

```text
logs/phase1234_live_view/
```

## Return and recovery test

1. Enter the frame and wait until the overlay shows a `T` and `V` value.
2. For an enrolled visitor, wait for `NAME=Rico`.
3. Leave completely for longer than two seconds but less than sixty seconds.
4. Re-enter and look toward the camera.

Expected behavior:

```text
track_id:           allowed to change, for example T12 -> T17
visitor_session_id: expected to recover, same V code
identity:           Rico remains or is restored quickly
```

The debug header increments `recovered=<count>` when a visitor session is reconnected. Exact
recognition latency and false-match behavior must be validated on the actual exhibition camera and,
later, with multiple participants.

## Identity management

List:

```powershell
.\scripts\manage_identity.ps1 -Action list
```

Delete an obsolete duplicate row using its actual ID:

```powershell
.\scripts\manage_identity.ps1 `
  -Action delete `
  -IdentityId "person_replace_with_actual_id"
```

Old duplicate display-name rows are pooled during matching, but deleting unwanted historical rows is
still recommended for a clean gallery.

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
GET    /api/v1/tracks              includes visitor_sessions
GET    /api/v1/faces               includes visitor_sessions
GET    /api/v1/identities          includes visitor_sessions
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

Stop the live pipeline before running a camera probe because both operations own camera index `1`.

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
