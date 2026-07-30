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

Phase 2 uses an ONNX-exported OSNet x0.25 model for anonymous short-term person
re-identification. The official model repository publishes PyTorch weights rather than the
required ONNX artifact, so the repository provides a one-time isolated exporter:

```powershell
.\scripts\export_osnet_x0_25_onnx.ps1
```

Expected path:

```text
models/osnet_x0_25.onnx
```

The exporter uses the official Torchreid OSNet architecture and MSMT17-trained
`osnet_x0_25` weights. It creates a separate `visionedge-osnet-export` Conda environment, so
PyTorch is not added to the working `smartoffice` environment.

When this ONNX file is absent, Phase 2 remains testable with the explicitly reported
`hsv_histogram` fallback. The fallback is not equivalent to neural ReID.

## YuNet face detector and SFace face recognizer

Phases 3 and 4 use the official OpenCV Zoo ONNX models:

```powershell
.\scripts\download_face_models.ps1
```

Expected files:

```text
models/face_detection_yunet_2023mar.onnx
models/face_recognition_sface_2021dec.onnx
```

The download script uses the OpenCV Zoo model files:

```text
https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
```

YuNet supplies a face box, five landmarks, and detection confidence. SFace converts a
quality-approved, aligned face into a local embedding. Model inference uses OpenCV DNN;
YOLOX and OSNet continue to use the already validated ONNX Runtime CUDA sessions.

The identity database stores normalized embeddings and consent metadata only. It does not
store face photographs. The local database is under the ignored `data/` directory.
