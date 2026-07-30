# Complete Phase 1-4 quick test

Use this after Phase 3 and Phase 4 code is pulled and the four model files exist.

## Pull and verify models

```powershell
cd D:\smart-office-agent
git switch Vision-Edge-Server
git pull --ff-only origin Vision-Edge-Server
cd .\vision-edge-server

Get-Item .\models\yolox_nano.onnx
Get-Item .\models\osnet_x0_25.onnx
Get-Item .\models\face_detection_yunet_2023mar.onnx
Get-Item .\models\face_recognition_sface_2021dec.onnx
```

Download the face models when the last two files are absent:

```powershell
.\scripts\download_face_models.ps1
```

## Unit tests

```powershell
conda activate smartoffice
python -m pytest -q
```

## Start the server

```powershell
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Keep this terminal open.

## Automated complete-pipeline smoke test

Open a second terminal:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server
.\scripts\run_phase1234_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

This requires:

- 4K MSMF camera capture;
- CUDA YOLOX inference;
- OSNet ONNX appearance backend;
- tracking updates;
- YuNet model and analysis cycles;
- SFace model and local identity database;
- valid debug JPEG.

Then stand in front of the camera and run:

```powershell
.\scripts\run_phase1234_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -RequireFace
```

## Live evaluation

```powershell
.\scripts\run_phase1234_live_view.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe" `
  -WindowWidth 960 `
  -WindowHeight 540
```

Identity enrollment and deletion endpoints accept mutation requests only from the local RTX
machine. Remote i5 clients may consume status, track and WebSocket results but cannot change the
local identity gallery.
