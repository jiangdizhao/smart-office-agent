# Phase 1 validation on the RTX laptop

Run these commands from Windows PowerShell.

## 1. Pull the branch

```powershell
cd D:\smart-office-agent
git switch Vision-Edge-Server
git pull --ff-only origin Vision-Edge-Server
git rev-parse --short HEAD
```

## 2. Verify the existing environment

Do not force-reinstall the shared Smart Office environment when it already passes the hardware probe.

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
python -m pip check
python -c "import sys, cv2, numpy, onnxruntime as ort; print(sys.executable); print('OpenCV', cv2.__version__); print('NumPy', numpy.__version__); print('ORT', ort.__version__); print('Providers', ort.get_available_providers())"
```

The provider list must contain `CUDAExecutionProvider`.

## 3. Download the detector model

```powershell
.\scripts\download_yolox_nano.ps1
Get-Item .\models\yolox_nano.onnx
```

## 4. Run hardware-independent tests

```powershell
python -m pytest -q
```

## 5. Start the live service

Make sure no other process is using camera index 1.

```powershell
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Keep this terminal open.

## 6. Run the automated live smoke test

Open a second terminal:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\run_phase1_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Expected result:

```text
PASS: Phase 1 camera and person-detection pipeline is ready.
```

## 7. Inspect the annotated image

Open:

```text
http://127.0.0.1:8015/api/v1/debug/stream.mjpg
```

The image should contain green person boxes and a yellow engagement-zone polygon.

Useful JSON endpoints:

```text
http://127.0.0.1:8015/api/v1/status
http://127.0.0.1:8015/api/v1/detections
```

## 8. Verify presence events

Open a third terminal:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\watch_vision_events.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Then perform this sequence:

1. leave the camera view empty;
2. enter the view;
3. move closer until the person box becomes sufficiently large;
4. leave the view for more than 1.5 seconds.

Expected event types include:

```text
visitor_entered
visitor_engaged
visitor_left
```

With multiple visible people, `group_detected` may also be emitted.

Stop the watcher with `Ctrl+C`.

## 9. Stop or restart the pipeline

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8015/api/v1/vision/stop
Invoke-RestMethod -Method Post http://127.0.0.1:8015/api/v1/vision/start
Invoke-RestMethod -Method Post http://127.0.0.1:8015/api/v1/vision/restart
```

A camera hardware probe cannot run while the live pipeline owns the camera. Stop the pipeline first when re-running the Phase 0 camera probe.
