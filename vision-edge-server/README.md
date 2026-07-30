# RTX Vision Edge Server

This directory is a standalone service inside the `Vision-Edge-Server` branch. It is intentionally isolated from the inherited Smart Office task runtime.

## Phase 0 scope

Implemented:

- FastAPI service skeleton;
- `GET /health`;
- `GET /api/v1/status`;
- `GET /api/v1/config/public`;
- `POST /api/v1/probe`;
- `WS /ws/v1/events` with `server_ready`, `state_snapshot`, heartbeat, ping/pong, and reconnect snapshot behavior;
- ONNX Runtime CUDA Execution Provider probe;
- `nvidia-smi` GPU, driver, memory, temperature, and power-state probe;
- Windows camera mode benchmark across configured backend, FOURCC, and FPS combinations;
- requested-versus-actual camera resolution, reported FPS, measured FPS, FOURCC, backend, frame shape, and read-latency reporting;
- automatic selection of the best realtime-capable camera mode;
- five-minute sustained camera benchmark with latency percentiles and failure statistics;
- JSON structured application logs;
- PowerShell startup and hardware-probe scripts;
- automated HTTP/WebSocket contract tests and a live smoke test.

Not implemented in Phase 0:

- frame streaming or resize pipeline;
- person or face detection;
- tracking;
- visitor identity;
- debug video dashboard;
- Smart Office client integration.

The USB camera is requested at 3840×2160. Later phases will retain only the latest source frame, create a low-resolution global-detection copy, and use high-resolution regions only for face processing.

## Environment

A dedicated Python 3.11+ environment is recommended:

```powershell
conda create -n visionedge python=3.11 -y
conda activate visionedge
cd D:\smart-office-agent\vision-edge-server
python -m pip install --upgrade pip
python -m pip install -r .\requirements-vision.txt
```

The Vision dependencies pin `numpy<2` and `protobuf<5` because the service may share an environment with binary extensions and MediaPipe packages that require those ABI/dependency ranges.

If the existing `smartoffice` environment is used, repair any previously upgraded protobuf before continuing:

```powershell
conda activate smartoffice
python -m pip install --upgrade --force-reinstall "numpy>=1.26,<2" "protobuf>=4.25.8,<5"
python -m pip install -r .\requirements-vision.txt
python -m pip check
```

The PowerShell scripts resolve Python in this order:

1. an explicitly supplied `-PythonExe`;
2. `$env:CONDA_PREFIX\python.exe` from the active Conda environment;
3. `python` from `PATH`.

Each script prints the actual `sys.executable` before running. This avoids a nested Windows PowerShell process silently selecting the Anaconda base interpreter.

## Probe hardware before starting the service

From an activated Conda environment, direct invocation is preferred:

```powershell
cd D:\smart-office-agent\vision-edge-server
.\scripts\probe_hardware.ps1 -Mode all
```

Nested PowerShell invocation is also supported because the script resolves `$env:CONDA_PREFIX\python.exe`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\probe_hardware.ps1 -Mode all
```

Explicit interpreter example:

```powershell
.\scripts\probe_hardware.ps1 -Mode all -PythonExe "D:\anaconda3\envs\visionedge\python.exe"
```

Expected GPU evidence includes:

```text
CUDAExecutionProvider
RTX 3070 Ti Laptop GPU
```

The first lines of the script output must show the intended interpreter, for example:

```text
Conda environment: smartoffice
Python: D:\anaconda3\envs\smartoffice\python.exe
```

## Camera probe version 2

The default camera is `device_index: 1`. The probe benchmarks these modes in order, but evaluates all successful attempts before selecting the best one:

```text
DSHOW + MJPG + 3840×2160 @ 30 FPS
DSHOW + MJPG + 3840×2160 @ 15 FPS
DSHOW + YUY2 + 3840×2160 @ 15 FPS
MSMF  + MJPG + 3840×2160 @ 30 FPS
MSMF  + MJPG + 3840×2160 @ 15 FPS
```

Each attempt reports:

- requested backend, FOURCC, resolution, and FPS;
- whether each OpenCV property setter was accepted;
- actual backend, FOURCC, resolution, and driver-reported FPS;
- measured FPS based on successful frame reads and elapsed wall-clock time;
- mean and maximum blocking read latency;
- resolution match and realtime-capability status.

The selection order is:

1. `ready` before `degraded` before `unusable_for_realtime`;
2. exact 3840×2160 resolution before a driver fallback resolution within the same status class;
3. higher measured FPS;
4. higher successful-read ratio;
5. lower mean read latency.

Default classification thresholds:

```text
measured_fps >= 10  -> ready
measured_fps >= 3   -> degraded
measured_fps < 3    -> unusable_for_realtime
```

The top-level `selected_mode`, `actual`, `sample`, and `performance` fields describe the chosen attempt. The complete `attempts` array remains available for diagnosis. Driver-reported FPS is never treated as proof of realtime performance.

## Sustained camera benchmark

The short probe is intended to select a viable capture mode. Use the sustained benchmark to verify that the selected MSMF 4K path remains stable over time.

The default run lasts five minutes and uses camera index and resolution from `config/vision.yaml`, with `MSMF`, automatic media-format negotiation, and a requested 30 FPS:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\run_camera_benchmark.ps1 `
  -DurationSeconds 300 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

A shorter one-minute validation is also available:

```powershell
.\scripts\run_camera_benchmark.ps1 -DurationSeconds 60
```

The benchmark prints progress every ten seconds and saves a JSON report under the ignored `vision-edge-server/logs/` directory. The report includes:

- sustained measured read throughput;
- attempted, successful, and failed reads;
- success ratio;
- mean, P50, P95, P99, and maximum blocking read latency;
- counts of reads exceeding 100 ms and 200 ms;
- maximum consecutive read failures;
- resolution-change count;
- actual backend, driver-reported FPS, raw FOURCC value, and sanitized FOURCC text;
- final `ready`, `degraded`, `unusable_for_realtime`, or `interrupted` status.

The default ready criteria are:

```text
measured FPS >= 10
success ratio >= 99%
maximum consecutive read failures <= 3
```

The default command deliberately uses `-Fourcc AUTO` because the successful MSMF mode negotiates its media format internally. To test another path explicitly:

```powershell
.\scripts\run_camera_benchmark.ps1 `
  -Backend DSHOW `
  -Fourcc YUY2 `
  -Fps 15 `
  -DurationSeconds 60
```

Pressing `Ctrl+C` saves a partial report with status `interrupted` before the camera is released.

## Start

```powershell
cd D:\smart-office-agent\vision-edge-server
.\scripts\start_vision_server.ps1
```

Or:

```powershell
$env:VISION_CONFIG_PATH = "D:\somewhere\vision.yaml"
python -m app
```

Endpoints:

```text
http://127.0.0.1:8015/health
http://127.0.0.1:8015/api/v1/status
http://127.0.0.1:8015/docs
ws://127.0.0.1:8015/ws/v1/events
```

## Live smoke test

Keep the server running, then open another terminal:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\run_smoke_test.ps1
```

Expected final line:

```text
PASS: Phase 0 health, status, WebSocket snapshot, and heartbeat contract are available.
```

## Unit and contract tests

These tests do not require an NVIDIA GPU or a camera:

```powershell
cd D:\smart-office-agent\vision-edge-server
python -m pytest
```

## Access from the i5 Smart Office client

Find the RTX laptop IPv4 address and test from the i5 machine:

```powershell
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/health
Invoke-RestMethod http://<RTX-LAPTOP-IP>:8015/api/v1/status
```

Windows Firewall may need an inbound TCP rule for port 8015. Restrict the rule to the exhibition LAN profile rather than exposing the service publicly.

## Configuration

Default configuration: `config/vision.yaml`.

Override it without modifying code:

```powershell
$env:VISION_CONFIG_PATH = "D:\somewhere\vision.yaml"
```

The service starts even when a camera or CUDA probe fails. `/health` remains a liveness endpoint, while `/api/v1/status` reports the detailed degraded condition. Set `gpu.require_cuda: true` only after the CUDA environment is verified.

## Protocol

See [VISION_EVENT_PROTOCOL_V1.md](./VISION_EVENT_PROTOCOL_V1.md).
