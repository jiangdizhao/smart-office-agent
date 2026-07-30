from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from app.config import ReIdSettings
from app.ort_cuda import prepare_cuda_runtime


class AppearanceExtractor:
    """Optional OSNet ONNX embedding with a deterministic HSV fallback.

    The fallback keeps Phase 2 testable before the optional OSNet model is exported. It is
    deliberately labelled in status output so it cannot be mistaken for neural ReID.
    """

    def __init__(self, settings: ReIdSettings, server_root: Path) -> None:
        self.settings = settings
        configured = Path(settings.model_path)
        self.model_path = configured if configured.is_absolute() else server_root / configured
        self.session: Any | None = None
        self.input_name: str | None = None
        self.output_names: list[str] = []
        self.providers: list[str] = []
        self.backend = "disabled" if not settings.enabled else "uninitialized"
        self.last_error: str | None = None
        self.embedding_count = 0
        self.last_embedding_ms: float | None = None
        self.cuda_runtime: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.backend in {"osnet_onnx", "hsv_histogram"}

    def load(self) -> None:
        if not self.settings.enabled:
            self.backend = "disabled"
            return
        if not self.model_path.exists():
            if self.settings.allow_histogram_fallback:
                self.backend = "hsv_histogram"
                self.last_error = f"OSNet model is missing: {self.model_path}"
                return
            raise RuntimeError(f"OSNet model is missing: {self.model_path}")

        try:
            import onnxruntime as ort

            self.cuda_runtime = prepare_cuda_runtime(ort)
            available = set(ort.get_available_providers())
            providers = [item for item in self.settings.providers if item in available]
            if self.settings.require_cuda and "CUDAExecutionProvider" not in providers:
                raise RuntimeError(
                    f"CUDAExecutionProvider is required for ReID; available={sorted(available)}"
                )
            self.session = ort.InferenceSession(str(self.model_path), providers=providers)
            self.providers = list(self.session.get_providers())
            if self.settings.require_cuda and "CUDAExecutionProvider" not in self.providers:
                raise RuntimeError(f"OSNet session providers={self.providers}")
            inputs = self.session.get_inputs()
            if len(inputs) != 1:
                raise RuntimeError(f"Expected one OSNet input, found {len(inputs)}")
            self.input_name = inputs[0].name
            self.output_names = [output.name for output in self.session.get_outputs()]
            self.backend = "osnet_onnx"
            self.last_error = None
        except Exception as exc:
            self.session = None
            self.input_name = None
            self.output_names = []
            self.providers = []
            detail = f"{type(exc).__name__}: {exc}"
            if not self.settings.allow_histogram_fallback:
                raise RuntimeError(detail) from exc
            self.backend = "hsv_histogram"
            self.last_error = detail

    def close(self) -> None:
        self.session = None
        self.input_name = None
        self.output_names = []
        self.providers = []

    def extract(self, source_frame: np.ndarray, bbox: dict[str, float]) -> np.ndarray | None:
        crop = self._crop(source_frame, bbox)
        if crop is None:
            return None
        started = time.perf_counter()
        if self.backend == "osnet_onnx":
            feature = self._extract_osnet(crop)
        elif self.backend == "hsv_histogram":
            feature = self._extract_histogram(crop)
        else:
            return None
        self.embedding_count += 1
        self.last_embedding_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return feature

    def _crop(self, frame: np.ndarray, bbox: dict[str, float]) -> np.ndarray | None:
        height, width = frame.shape[:2]
        x1 = int(max(0.0, bbox["x"] - self.settings.crop_margin) * width)
        y1 = int(max(0.0, bbox["y"] - self.settings.crop_margin) * height)
        x2 = int(min(1.0, bbox["x"] + bbox["width"] + self.settings.crop_margin) * width)
        y2 = int(min(1.0, bbox["y"] + bbox["height"] + self.settings.crop_margin) * height)
        if x2 - x1 < self.settings.min_crop_width or y2 - y1 < self.settings.min_crop_height:
            return None
        crop = frame[y1:y2, x1:x2]
        return crop if crop.size else None

    def _extract_osnet(self, crop: np.ndarray) -> np.ndarray:
        if self.session is None or self.input_name is None:
            raise RuntimeError("OSNet session is not loaded")
        import cv2

        image = cv2.resize(
            crop,
            (self.settings.input_width, self.settings.input_height),
            interpolation=cv2.INTER_LINEAR,
        )
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        mean = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
        tensor = ((image - mean) / std).transpose(2, 0, 1)[None, ...]
        outputs = self.session.run(self.output_names or None, {self.input_name: tensor})
        return _normalize(np.asarray(outputs[0], dtype=np.float32).reshape(-1))

    def _extract_histogram(self, crop: np.ndarray) -> np.ndarray:
        import cv2

        resized = cv2.resize(crop, (96, 192), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist(
            [hsv],
            [0, 1],
            None,
            [self.settings.histogram_h_bins, self.settings.histogram_s_bins],
            [0, 180, 0, 256],
        ).reshape(-1)
        # Add coarse vertical colour structure to reduce collisions between similar clothes.
        stripes: list[np.ndarray] = []
        for stripe in np.array_split(hsv, 3, axis=0):
            part = cv2.calcHist([stripe], [0, 1], None, [8, 8], [0, 180, 0, 256]).reshape(-1)
            stripes.append(part)
        return _normalize(np.concatenate([hist, *stripes]).astype(np.float32))

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.enabled,
            "ready": self.ready,
            "backend": self.backend,
            "model_path": str(self.model_path),
            "model_exists": self.model_path.exists(),
            "providers": self.providers,
            "embedding_count": self.embedding_count,
            "last_embedding_ms": self.last_embedding_ms,
            "last_error": self.last_error,
            "cuda_runtime": self.cuda_runtime,
        }


def _normalize(feature: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(feature))
    if norm <= 1e-12:
        return feature.astype(np.float32)
    return (feature / norm).astype(np.float32)


def cosine_similarity(left: np.ndarray | None, right: np.ndarray | None) -> float | None:
    if left is None or right is None or left.size != right.size:
        return None
    return float(np.clip(np.dot(left, right), -1.0, 1.0))
