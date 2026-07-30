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
- Windows camera probe across DirectShow, Media Foundation, and automatic backends;
- requested-versus-actual camera resolution, FPS, FOURCC, backend, frame shape, and read-latency reporting;
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

The camera probe requests 3840×2160 because the current USB camera cannot be switched to 1080p. Later phases will retain only the latest source frame, create a low-resolution global-detection copy, and use high-resolution regions only for face processing. The probe reports the actual mode accepted by the driver.

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

Nested PowerShell invocation is also supported because the script now resolves `$env:CONDA_PREFIX\python.exe`:

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

The camera report must be read from its `actual` fields; requested settings are not treated as proof that the driver accepted them.

## Start

```powershell
cd D:\smart-office-agent\vision-edge-server
.\scripts\start_vision_server.ps1
```

Or:

```powershell
$env:VISION_CONFIG_PATH = "D:\smart-office-agent\vision-edge-server\config\vision.yaml"
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
