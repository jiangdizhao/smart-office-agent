from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(StrictModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8015, ge=1, le=65535)
    heartbeat_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()


class CameraProbeMode(StrictModel):
    backend: Literal["DSHOW", "MSMF", "ANY"]
    fourcc: str = "AUTO"
    fps: float = Field(gt=0)

    @field_validator("fourcc")
    @classmethod
    def normalize_fourcc(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "AUTO" and len(normalized) != 4:
            raise ValueError("camera FOURCC must be AUTO or exactly four characters")
        return normalized


def _default_camera_probe_modes() -> list[CameraProbeMode]:
    return [
        CameraProbeMode(backend="DSHOW", fourcc="MJPG", fps=30),
        CameraProbeMode(backend="DSHOW", fourcc="MJPG", fps=15),
        CameraProbeMode(backend="DSHOW", fourcc="YUY2", fps=15),
        CameraProbeMode(backend="MSMF", fourcc="MJPG", fps=30),
        CameraProbeMode(backend="MSMF", fourcc="MJPG", fps=15),
    ]


class CameraSettings(StrictModel):
    enabled: bool = True
    probe_on_startup: bool = False
    device_index: int = Field(default=1, ge=0)
    requested_width: int = Field(default=3840, ge=1)
    requested_height: int = Field(default=2160, ge=1)
    probe_modes: list[CameraProbeMode] = Field(default_factory=_default_camera_probe_modes, min_length=1)
    warmup_frames: int = Field(default=2, ge=0, le=60)
    sample_frames: int = Field(default=6, ge=2, le=60)
    ready_min_measured_fps: float = Field(default=10.0, gt=0)
    degraded_min_measured_fps: float = Field(default=3.0, gt=0)
    inter_attempt_delay_seconds: float = Field(default=0.25, ge=0, le=5.0)
    runtime_backend: Literal["DSHOW", "MSMF", "ANY"] = "MSMF"
    runtime_fourcc: str = "AUTO"
    runtime_fps: float = Field(default=30.0, gt=0, le=120)
    reconnect_delay_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    reopen_after_consecutive_failures: int = Field(default=10, ge=1, le=1000)
    stats_window_frames: int = Field(default=300, ge=30, le=5000)

    @field_validator("runtime_fourcc")
    @classmethod
    def normalize_runtime_fourcc(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "AUTO" and len(normalized) != 4:
            raise ValueError("runtime_fourcc must be AUTO or exactly four characters")
        return normalized

    @model_validator(mode="after")
    def validate_performance_thresholds(self) -> CameraSettings:
        if self.degraded_min_measured_fps >= self.ready_min_measured_fps:
            raise ValueError("degraded_min_measured_fps must be lower than ready_min_measured_fps")
        return self


class GpuSettings(StrictModel):
    probe_on_startup: bool = True
    require_cuda: bool = False
    nvidia_smi_timeout_seconds: float = Field(default=5.0, gt=0, le=30.0)


class VisionSettings(StrictModel):
    enabled: bool = True
    start_on_startup: bool = True
    global_width: int = Field(default=960, ge=160, le=3840)
    global_height: int = Field(default=540, ge=90, le=2160)


class DetectionSettings(StrictModel):
    enabled: bool = True
    model_path: str = "models/yolox_nano.onnx"
    input_width: int = Field(default=416, ge=32, le=2048)
    input_height: int = Field(default=416, ge=32, le=2048)
    inference_hz: float = Field(default=12.0, gt=0, le=60)
    score_threshold: float = Field(default=0.10, ge=0, le=1)
    nms_threshold: float = Field(default=0.45, ge=0, le=1)
    person_class_id: int = Field(default=0, ge=0)
    max_detections: int = Field(default=20, ge=1, le=500)
    providers: list[str] = Field(
        default_factory=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"], min_length=1
    )
    require_cuda: bool = True


class ReIdSettings(StrictModel):
    enabled: bool = True
    model_path: str = "models/osnet_x0_25.onnx"
    input_width: int = Field(default=128, ge=32, le=1024)
    input_height: int = Field(default=256, ge=64, le=2048)
    providers: list[str] = Field(
        default_factory=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"], min_length=1
    )
    require_cuda: bool = True
    allow_histogram_fallback: bool = True
    crop_margin: float = Field(default=0.02, ge=0, le=0.25)
    min_crop_width: int = Field(default=32, ge=8, le=2048)
    min_crop_height: int = Field(default=80, ge=8, le=4096)
    histogram_h_bins: int = Field(default=16, ge=4, le=64)
    histogram_s_bins: int = Field(default=16, ge=4, le=64)


class TrackingSettings(StrictModel):
    enabled: bool = True
    low_threshold: float = Field(default=0.10, ge=0, le=1)
    high_threshold: float = Field(default=0.50, ge=0, le=1)
    new_track_threshold: float = Field(default=0.60, ge=0, le=1)
    confirm_hits: int = Field(default=3, ge=1, le=30)
    confirm_window_frames: int = Field(default=5, ge=1, le=120)
    lost_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30)
    removed_retention_seconds: float = Field(default=3.0, ge=0, le=60)
    gallery_size: int = Field(default=10, ge=1, le=100)
    feature_ema_alpha: float = Field(default=0.10, gt=0, le=1)
    iou_weight: float = Field(default=0.45, ge=0, le=1)
    motion_weight: float = Field(default=0.20, ge=0, le=1)
    appearance_weight: float = Field(default=0.35, ge=0, le=1)
    first_stage_max_cost: float = Field(default=0.82, ge=0, le=2)
    second_stage_max_cost: float = Field(default=0.78, ge=0, le=2)
    tentative_max_cost: float = Field(default=0.72, ge=0, le=2)
    max_center_distance: float = Field(default=0.25, gt=0, le=1.5)
    lost_max_center_distance: float = Field(default=0.40, gt=0, le=2)
    minimum_iou_gate: float = Field(default=0.01, ge=0, le=1)
    mahalanobis_gate: float = Field(default=18.47, gt=0)
    appearance_similarity_gate: float = Field(default=0.72, ge=-1, le=1)

    @model_validator(mode="after")
    def validate_thresholds(self) -> TrackingSettings:
        if not self.low_threshold <= self.high_threshold <= self.new_track_threshold:
            raise ValueError("tracking thresholds must satisfy low <= high <= new_track")
        if self.iou_weight + self.motion_weight + self.appearance_weight <= 0:
            raise ValueError("at least one tracking association weight must be positive")
        return self


class PresenceSettings(StrictModel):
    enter_confirm_frames: int = Field(default=2, ge=1, le=120)
    engage_confirm_frames: int = Field(default=3, ge=1, le=120)
    left_timeout_seconds: float = Field(default=1.5, ge=0.1, le=60)
    engagement_min_area_ratio: float = Field(default=0.08, ge=0, le=1)
    engagement_zone: list[tuple[float, float]] = Field(
        default_factory=lambda: [(0.05, 0.05), (0.95, 0.05), (0.95, 1.0), (0.05, 1.0)],
        min_length=3,
    )

    @field_validator("engagement_zone")
    @classmethod
    def validate_zone(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for x, y in value:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("engagement_zone coordinates must be normalized to [0, 1]")
        return value


class PrimarySettings(StrictModel):
    acquire_stable_seconds: float = Field(default=0.5, ge=0, le=10)
    challenger_margin: float = Field(default=0.20, ge=0, le=1)
    challenger_hold_seconds: float = Field(default=1.0, ge=0, le=10)
    lost_lock_seconds: float = Field(default=2.0, ge=0, le=30)
    area_full_scale_ratio: float = Field(default=0.25, gt=0, le=1)
    dwell_full_scale_seconds: float = Field(default=5.0, gt=0, le=120)
    stability_full_scale_hits: int = Field(default=20, ge=1, le=1000)
    target_x: float = Field(default=0.5, ge=0, le=1)
    target_y: float = Field(default=0.85, ge=0, le=1)


class DebugSettings(StrictModel):
    enabled: bool = True
    preview_width: int = Field(default=960, ge=160, le=3840)
    preview_height: int = Field(default=540, ge=90, le=2160)
    preview_fps: float = Field(default=10.0, gt=0, le=30)
    jpeg_quality: int = Field(default=80, ge=20, le=100)
    draw_zone: bool = True
    draw_raw_detections: bool = False
    draw_track_trails: bool = True
    draw_lost_tracks: bool = True


class AppConfig(StrictModel):
    service_name: str = "rtx-vision-edge-server"
    version: str = "0.3.0"
    phase: str = "phase2_multi_object_tracking"
    server: ServerSettings = Field(default_factory=ServerSettings)
    camera: CameraSettings = Field(default_factory=CameraSettings)
    gpu: GpuSettings = Field(default_factory=GpuSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
    reid: ReIdSettings = Field(default_factory=ReIdSettings)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)
    presence: PresenceSettings = Field(default_factory=PresenceSettings)
    primary: PrimarySettings = Field(default_factory=PrimarySettings)
    debug: DebugSettings = Field(default_factory=DebugSettings)


def default_config_path() -> Path:
    configured = os.getenv("VISION_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / "config" / "vision.yaml").resolve()


def load_config(path: str | Path | None = None) -> tuple[AppConfig, Path]:
    config_path = Path(path).expanduser().resolve() if path else default_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Vision config does not exist: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(raw), config_path
