# RTX Vision Edge Server

This directory is a standalone Windows vision service inside the `Vision-Edge-Server` branch. It is isolated from the inherited Smart Office task runtime and is intended to run on the RTX 3070 Ti laptop with USB camera index `1`.

## Current milestone

Version: `0.2.0`

Phase: `phase1_camera_person_detection`

Phase 0 established the FastAPI/WebSocket service, GPU probe, camera mode probe, and sustained camera benchmark. Phase 1 adds the live camera and person-presence pipeline.

## Phase 1 implementation

Implemented:

- dedicated MSMF camera-capture thread;
- verified runtime mode `device 1 + MSMF + AUTO + 3840×2160 @ 30 FPS`;
- latest-frame buffer: new frames overwrite old frames instead of building an inference queue;
- reconnect after repeated camera-read failures;
- 4K source frame resized to a `960×540` global frame;
- YOLOX-Nano ONNX person detection;
- ONNX Runtime provider order: CUDA first, CPU fallback configured;
- configurable inference rate, default `12 Hz`;
- normalized person boxes, confidence, area ratio, and bottom-center coordinates;
- aggregate presence state machine: `absent`, `present`, `engaged`;
- `visitor_entered`, `visitor_engaged`, `visitor_left`, and `group_detected` WebSocket events;
- annotated JPEG snapshot and MJPEG debug stream;
- start, stop, and restart API controls;
- unit tests for latest-frame buffering, YOLOX post-processing/NMS, polygon zones, and presence transitions;
- live Phase 1 smoke test.

Not implemented yet:

- stable per-person `track_id` values;
- multi-object tracking and primary-visitor locking;
- face detection, face quality, or identity recognition;
- persistent visitor database;
- Smart Office client integration.

Those items belong to later phases.

## Environment

The existing `smartoffice` Conda environment is supported. The PowerShell scripts resolve Python in this order:

1. explicitly supplied `-PythonExe`;
2. `$env:CONDA_PREFIX\python.exe` from the active Conda environment;
3. `python` from `PATH`.

Dependencies retain these compatibility constraints:

```text
numpy>=1.26,<2
protobuf>=4.25.8,<5
```

Install or verify the dependencies:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
python -m pip install -r .\requirements-vision.txt
python -m pip check
```

## Download the Phase 1 detector

The model binary is excluded from Git. Download the official YOLOX-Nano ONNX model:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\download_yolox_nano.ps1
```

Expected file:

```text
D:\smart-office-agent\vision-edge-server\models\yolox_nano.onnx
```

The script prints the downloaded file size and SHA-256 hash. Re-download only when necessary:

```powershell
.\scripts\download_yolox_nano.ps1 -Force
```

## Unit and contract tests

These tests do not require the camera or GPU:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
python -m pytest -q
```

The suite covers both Phase 0 and Phase 1 code.

## Start the Phase 1 server

Make sure no other program is using camera index `1`, then run:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Expected startup path:

```text
camera device 1
MSMF backend
3840×2160 source
30 FPS capture
960×540 global frame
YOLOX-Nano 416×416 inference
CUDAExecutionProvider
```

The server can start in a degraded state when the model is missing or CUDA model-session creation fails. The reason is visible under `vision.last_error` and `vision.detector.last_error` in `/api/v1/status`.

## Run the live Phase 1 smoke test

Keep the server running. Open a second PowerShell terminal:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\run_phase1_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

The test waits up to 45 seconds for:

- camera frames to arrive;
- the detector session to load;
- at least one inference to complete;
- the vision pipeline to report `ready`;
- the debug JPEG endpoint to return a valid image.

Expected first line at completion:

```text
PASS: Phase 1 camera and person-detection pipeline is ready.
```

The test does not require a person to be visible. It reports the current person count for reference.

## Manual person-detection test

Open the annotated stream in a browser:

```text
http://127.0.0.1:8015/api/v1/debug/stream.mjpg
```

Or open one current frame:

```text
http://127.0.0.1:8015/api/v1/debug/frame.jpg
```

Then test the presence transitions:

1. keep the camera view empty until the overlay shows `state=absent`;
2. walk into view and confirm a green person box appears;
3. approach until the person area exceeds the configured engagement threshold;
4. leave the view for more than `1.5` seconds;
5. inspect WebSocket events for `visitor_entered`, `visitor_engaged`, and `visitor_left`.

Current detections are also available at:

```text
http://127.0.0.1:8015/api/v1/detections
```

## Main endpoints

```text
GET  /health
GET  /api/v1/status
GET  /api/v1/config/public
POST /api/v1/probe
POST /api/v1/vision/start
POST /api/v1/vision/stop
POST /api/v1/vision/restart
GET  /api/v1/detections
GET  /api/v1/debug/frame.jpg
GET  /api/v1/debug/stream.mjpg
WS   /ws/v1/events
```

Interactive API documentation:

```text
http://127.0.0.1:8015/docs
```

## Camera probe and sustained benchmark

The Phase 0 tools remain available. The live Phase 1 pipeline owns the camera, so stop it before rerunning a camera probe:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8015/api/v1/vision/stop
.\scripts\probe_hardware.ps1 -Mode camera
```

Five-minute sustained capture benchmark:

```powershell
.\scripts\run_camera_benchmark.ps1 `
  -DurationSeconds 300 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

The verified hardware result was 9001 successful 4K frames from 9001 attempts over 300 seconds, with approximately 30 FPS and no read failure. The generated JSON reports remain under the ignored `logs/` directory.

## Important configuration

Default file:

```text
config/vision.yaml
```

Key values:

```yaml
camera:
  device_index: 1
  runtime_backend: MSMF
  runtime_fourcc: AUTO
  requested_width: 3840
  requested_height: 2160
  runtime_fps: 30

vision:
  global_width: 960
  global_height: 540

detection:
  model_path: models/yolox_nano.onnx
  input_width: 416
  input_height: 416
  inference_hz: 12
  score_threshold: 0.35
  nms_threshold: 0.45
  providers:
    - CUDAExecutionProvider
    - CPUExecutionProvider
  require_cuda: true

presence:
  enter_confirm_frames: 2
  engage_confirm_frames: 3
  left_timeout_seconds: 1.5
  engagement_min_area_ratio: 0.08
```

The yellow polygon in the debug image is the normalized engagement zone. The green boxes are person detections.

## Access from the i5 client

From the i5 machine on the exhibition LAN:

```powershell
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/health
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/api/v1/status
```

Debug stream:

```text
http://<RTX-LAPTOP-IP>:8015/api/v1/debug/stream.mjpg
```

Windows Firewall may require an inbound TCP rule for port `8015`. Restrict it to the exhibition LAN profile.

## Protocol

See [VISION_EVENT_PROTOCOL_V1.md](./VISION_EVENT_PROTOCOL_V1.md).
