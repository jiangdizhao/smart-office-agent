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

The camera probe requests 3840×2160 because the current USB camera cannot be switched to 1080p. Later phases will retain only the latest 4K frame, create a low-resolution global-detection copy, and use high-resolution regions only for face processing.

## Environment

Use a dedicated Python 3.11+ environment instead of the existing Smart Office environment.

```powershell
conda create -n visionedge python=3.11 -y
conda activate visionedge
cd D:\smart-office-agent\vision-edge-server
python -m pip install --upgrade pip
python -m pip install -r .\requirements-vision.txt
```

The start script does not assume a Conda environment name. It uses the currently selected `python`, or an explicitly supplied Python executable.

## Probe hardware before starting the service

```powershell
cd D:\smart-office-agent\vision-edge-server
powershell -ExecutionPolicy Bypass -File .\scripts\probe_hardware.ps1 -Mode all
```

Explicit interpreter example:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\probe_hardware.ps1 -Mode all -PythonExe "D:\anaconda3\envs\visionedge\python.exe"
```

Expected GPU evidence includes:

```text
CUDAExecutionProvider
RTX 3070 Ti Laptop GPU
```

The camera report must be read from its `actual` fields; requested settings are not treated as proof that the driver accepted them.

## Start

```powershell
cd D:\smart-office-agent\vision-edge-server
powershell -ExecutionPolicy Bypass -File .\scripts\start_vision_server.ps1
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
cd D:\smart-office-agent\vision-edge-server
powershell -ExecutionPolicy Bypass -File .\scripts\run_smoke_test.ps1
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
