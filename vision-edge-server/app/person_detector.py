from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from app.config import DetectionSettings


class DetectorUnavailableError(RuntimeError):
    pass


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    if boxes.size == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1 + 1.0) * np.maximum(0.0, y2 - y1 + 1.0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[index], x1[order[1:]])
        yy1 = np.maximum(y1[index], y1[order[1:]])
        xx2 = np.minimum(x2[index], x2[order[1:]])
        yy2 = np.minimum(y2[index], y2[order[1:]])
        intersection = np.maximum(0.0, xx2 - xx1 + 1.0) * np.maximum(
            0.0, yy2 - yy1 + 1.0
        )
        union = areas[index] + areas[order[1:]] - intersection
        overlap = np.divide(
            intersection,
            union,
            out=np.zeros_like(intersection),
            where=union > 0,
        )
        order = order[np.where(overlap <= threshold)[0] + 1]
    return keep


def _demo_postprocess(outputs: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    grids: list[np.ndarray] = []
    expanded_strides: list[np.ndarray] = []
    for stride in (8, 16, 32):
        hsize = input_size[0] // stride
        wsize = input_size[1] // stride
        xv, yv = np.meshgrid(np.arange(wsize), np.arange(hsize))
        grid = np.stack((xv, yv), axis=2).reshape(1, -1, 2)
        grids.append(grid)
        expanded_strides.append(np.full((*grid.shape[:2], 1), stride))
    grid = np.concatenate(grids, axis=1)
    strides = np.concatenate(expanded_strides, axis=1)
    decoded = outputs.copy()
    decoded[..., :2] = (decoded[..., :2] + grid) * strides
    decoded[..., 2:4] = np.exp(decoded[..., 2:4]) * strides
    return decoded


def _preprocess(image: np.ndarray, input_size: tuple[int, int]) -> tuple[np.ndarray, float]:
    import cv2

    height, width = image.shape[:2]
    ratio = min(input_size[0] / height, input_size[1] / width)
    resized = cv2.resize(
        image,
        (int(width * ratio), int(height * ratio)),
        interpolation=cv2.INTER_LINEAR,
    ).astype(np.uint8)
    padded = np.full((input_size[0], input_size[1], 3), 114, dtype=np.uint8)
    padded[: resized.shape[0], : resized.shape[1]] = resized
    return np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32), ratio


class YoloXPersonDetector:
    def __init__(self, settings: DetectionSettings, server_root: Path) -> None:
        self.settings = settings
        model_path = Path(settings.model_path)
        self.model_path = model_path if model_path.is_absolute() else server_root / model_path
        self.session: Any | None = None
        self.input_name: str | None = None
        self.output_names: list[str] = []
        self.providers: list[str] = []
        self.last_error: str | None = None
        self.inference_count = 0
        self.last_timings: dict[str, float] | None = None

    @property
    def ready(self) -> bool:
        return self.session is not None

    def load(self) -> None:
        if not self.model_path.exists():
            raise DetectorUnavailableError(
                f"YOLOX model is missing: {self.model_path}. "
                "Run scripts/download_yolox_nano.ps1."
            )
        try:
            import onnxruntime as ort
        except Exception as exc:
            raise DetectorUnavailableError(
                f"ONNX Runtime import failed: {type(exc).__name__}: {exc}"
            ) from exc

        available = set(ort.get_available_providers())
        providers = [provider for provider in self.settings.providers if provider in available]
        if self.settings.require_cuda and "CUDAExecutionProvider" not in providers:
            raise DetectorUnavailableError(
                "CUDAExecutionProvider is required but unavailable. "
                f"Available providers: {sorted(available)}"
            )
        if not providers:
            raise DetectorUnavailableError(
                f"None of the configured providers are available: {self.settings.providers}"
            )

        try:
            self.session = ort.InferenceSession(str(self.model_path), providers=providers)
        except Exception as exc:
            raise DetectorUnavailableError(
                f"Failed to load YOLOX model: {type(exc).__name__}: {exc}"
            ) from exc

        session_providers = list(self.session.get_providers())
        if self.settings.require_cuda and "CUDAExecutionProvider" not in session_providers:
            self.session = None
            raise DetectorUnavailableError(
                "YOLOX session did not activate CUDAExecutionProvider. "
                f"Session providers: {session_providers}"
            )

        inputs = self.session.get_inputs()
        if len(inputs) != 1:
            self.session = None
            raise DetectorUnavailableError(f"Expected one model input, found {len(inputs)}")
        self.input_name = inputs[0].name
        self.output_names = [output.name for output in self.session.get_outputs()]
        self.providers = session_providers
        self.last_error = None

    def close(self) -> None:
        self.session = None
        self.input_name = None
        self.output_names = []
        self.providers = []

    def detect(self, image: np.ndarray) -> tuple[list[dict[str, Any]], dict[str, float]]:
        if self.session is None or self.input_name is None:
            raise DetectorUnavailableError("Person detector is not loaded")

        input_size = (self.settings.input_height, self.settings.input_width)
        started = time.perf_counter()
        tensor, ratio = _preprocess(image, input_size)
        preprocess_ms = (time.perf_counter() - started) * 1000.0

        inference_started = time.perf_counter()
        outputs = self.session.run(
            self.output_names or None,
            {self.input_name: tensor[None, :, :, :]},
        )
        inference_ms = (time.perf_counter() - inference_started) * 1000.0

        post_started = time.perf_counter()
        predictions = _demo_postprocess(outputs[0], input_size)[0]
        boxes = predictions[:, :4]
        class_scores = predictions[:, 5:]
        if self.settings.person_class_id >= class_scores.shape[1]:
            raise DetectorUnavailableError(
                f"person_class_id={self.settings.person_class_id} is outside "
                f"model class count {class_scores.shape[1]}"
            )
        scores = predictions[:, 4] * class_scores[:, self.settings.person_class_id]
        valid = scores >= self.settings.score_threshold
        boxes, scores = boxes[valid], scores[valid]

        detections: list[dict[str, Any]] = []
        if boxes.size:
            boxes_xyxy = np.empty_like(boxes)
            boxes_xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
            boxes_xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
            boxes_xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
            boxes_xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
            boxes_xyxy /= ratio

            image_height, image_width = image.shape[:2]
            boxes_xyxy[:, [0, 2]] = np.clip(
                boxes_xyxy[:, [0, 2]], 0, image_width - 1
            )
            boxes_xyxy[:, [1, 3]] = np.clip(
                boxes_xyxy[:, [1, 3]], 0, image_height - 1
            )
            keep = _nms(boxes_xyxy, scores, self.settings.nms_threshold)[
                : self.settings.max_detections
            ]
            for index in keep:
                x1, y1, x2, y2 = (float(value) for value in boxes_xyxy[index])
                width = max(0.0, x2 - x1)
                height = max(0.0, y2 - y1)
                normalized = {
                    "x": x1 / image_width,
                    "y": y1 / image_height,
                    "width": width / image_width,
                    "height": height / image_height,
                }
                detections.append(
                    {
                        "class_id": self.settings.person_class_id,
                        "label": "person",
                        "score": round(float(scores[index]), 6),
                        "bbox": normalized,
                        "bbox_pixels": {
                            "x1": round(x1, 2),
                            "y1": round(y1, 2),
                            "x2": round(x2, 2),
                            "y2": round(y2, 2),
                        },
                        "area_ratio": round(
                            normalized["width"] * normalized["height"], 6
                        ),
                        "bottom_center": {
                            "x": round(
                                normalized["x"] + normalized["width"] / 2.0, 6
                            ),
                            "y": round(normalized["y"] + normalized["height"], 6),
                        },
                    }
                )

        postprocess_ms = (time.perf_counter() - post_started) * 1000.0
        timings = {
            "preprocess_ms": round(preprocess_ms, 3),
            "inference_ms": round(inference_ms, 3),
            "postprocess_ms": round(postprocess_ms, 3),
            "total_ms": round(preprocess_ms + inference_ms + postprocess_ms, 3),
        }
        self.inference_count += 1
        self.last_timings = timings
        self.last_error = None
        return detections, timings

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "model_path": str(self.model_path),
            "model_exists": self.model_path.exists(),
            "providers": self.providers,
            "configured_providers": self.settings.providers,
            "input_size": [self.settings.input_height, self.settings.input_width],
            "inference_hz": self.settings.inference_hz,
            "score_threshold": self.settings.score_threshold,
            "nms_threshold": self.settings.nms_threshold,
            "inference_count": self.inference_count,
            "last_timings": self.last_timings,
            "last_error": self.last_error,
        }
