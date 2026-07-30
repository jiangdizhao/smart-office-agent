# Phase 1 CUDA runtime repair

## Symptom

ONNX Runtime lists `CUDAExecutionProvider`, but YOLOX falls back to CPU and the server logs a missing DLL such as:

```text
cublasLt64_13.dll is missing
```

`get_available_providers()` only proves that the installed ONNX Runtime wheel was built with CUDA support. It does not prove that the CUDA and cuDNN runtime DLL dependencies can be loaded.

ONNX Runtime 1.28.x uses CUDA 13.0 and cuDNN 9.x. The project therefore installs the matching NVIDIA runtime wheels through the official `onnxruntime-gpu[cuda,cudnn]` extra and preloads them before creating a CUDA session.

## Repair

Stop the running Vision Server, then run:

```powershell
conda activate smartoffice
cd D:\smart-office-agent\vision-edge-server

.\scripts\install_ort_cuda_runtime.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

The script preserves the known-compatible shared-environment pins:

```text
numpy==1.26.4
protobuf==4.25.9
onnxruntime-gpu[cuda,cudnn]==1.28.0
```

It then runs `pip check`, creates a real YOLOX ONNX Runtime session with `CUDAExecutionProvider`, and performs one test inference.

Expected final line:

```text
PASS: ONNX Runtime created a CUDA session and completed a test inference.
```

## Manual verification

```powershell
python .\scripts\verify_ort_cuda.py --model .\models\yolox_nano.onnx
```

The JSON result must contain:

```json
{
  "session_providers": [
    "CUDAExecutionProvider",
    "CPUExecutionProvider"
  ]
}
```

## Re-test Phase 1

Start the server again:

```powershell
.\scripts\start_vision_server.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

In a second terminal:

```powershell
.\scripts\run_phase1_smoke_test.ps1 `
  -PythonExe "D:\anaconda3\envs\smartoffice\python.exe"
```

Expected result:

```text
PASS: Phase 1 camera and person-detection pipeline is ready.
```

The revised GPU probe now distinguishes a CUDA provider that is merely advertised by the wheel from one whose provider DLL and transitive runtime dependencies are actually loadable.
