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
    probe_on_startup: bool = True
    device_index: int = Field(default=1, ge=0)
    requested_width: int = Field(default=3840, ge=1)
    requested_height: int = Field(default=2160, ge=1)
    probe_modes: list[CameraProbeMode] = Field(default_factory=_default_camera_probe_modes, min_length=1)
    warmup_frames: int = Field(default=2, ge=0, le=60)
    sample_frames: int = Field(default=6, ge=2, le=60)
    ready_min_measured_fps: float = Field(default=10.0, gt=0)
    degraded_min_measured_fps: float = Field(default=3.0, gt=0)
    inter_attempt_delay_seconds: float = Field(default=0.25, ge=0, le=5.0)

    @model_validator(mode="after")
    def validate_performance_thresholds(self) -> CameraSettings:
        if self.degraded_min_measured_fps >= self.ready_min_measured_fps:
            raise ValueError("degraded_min_measured_fps must be lower than ready_min_measured_fps")
        return self


class GpuSettings(StrictModel):
    probe_on_startup: bool = True
    require_cuda: bool = False
    nvidia_smi_timeout_seconds: float = Field(default=5.0, gt=0, le=30.0)


class AppConfig(StrictModel):
    service_name: str = "rtx-vision-edge-server"
    version: str = "0.1.1"
    phase: str = "phase0_camera_probe_v2"
    server: ServerSettings = Field(default_factory=ServerSettings)
    camera: CameraSettings = Field(default_factory=CameraSettings)
    gpu: GpuSettings = Field(default_factory=GpuSettings)


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
