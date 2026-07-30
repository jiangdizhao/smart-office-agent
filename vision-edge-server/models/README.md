# Vision models

Binary models are intentionally excluded from Git.

## YOLOX-Nano person detector

Phase 1 uses the official YOLOX-Nano ONNX model from the Apache-2.0 licensed YOLOX project.

```powershell
.\scripts\download_yolox_nano.ps1
```

Expected path:

```text
models/yolox_nano.onnx
```

Official release source:

```text
https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx
```

## OSNet x0.25 appearance embedding

Phase 2 can use an ONNX-exported OSNet x0.25 model for short-term anonymous person
re-identification. The official model repository publishes PyTorch weights rather than the
required ONNX artifact, so the repository provides a one-time isolated exporter:

```powershell
.\scripts\export_osnet_x0_25_onnx.ps1
```

Expected path:

```text
models/osnet_x0_25.onnx
```

The exporter uses the official Torchreid OSNet architecture and the official MSMT17-trained
`osnet_x0_25` weights. It creates a separate `visionedge-osnet-export` Conda environment, so
PyTorch is not added to the working `smartoffice` environment.

When this ONNX file is absent, Phase 2 remains testable with the explicitly reported
`hsv_histogram` appearance fallback. The fallback is not equivalent to neural ReID and should
not be used to claim final occlusion-recovery accuracy.
